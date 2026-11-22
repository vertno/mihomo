package adapter

import (
	"context"
	"io"
	"net"
	"strings"
	"testing"
	"time"

	C "github.com/metacubex/mihomo/constant"
	"golang.org/x/net/http2"
)

// Read the first SETTINGS frame sent by the ordinary XHTTP client, over loopback.
func windowPeer(t *testing.T) (int, <-chan uint32) {
	t.Helper()
	listener, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	value := make(chan uint32, 1)
	stop := make(chan struct{})
	done := make(chan struct{})
	go func() {
		defer close(done)
		defer close(value)
		conn, err := listener.Accept()
		if err != nil {
			return
		}
		defer conn.Close()
		_ = conn.SetDeadline(time.Now().Add(8 * time.Second))
		preface := make([]byte, len(http2.ClientPreface))
		if _, err := io.ReadFull(conn, preface); err != nil || string(preface) != http2.ClientPreface {
			return
		}
		f := http2.NewFramer(conn, conn)
		if f.WriteSettings() != nil {
			return
		}
		frame, err := f.ReadFrame()
		if err != nil {
			return
		}
		settings, ok := frame.(*http2.SettingsFrame)
		if !ok {
			return
		}
		v, ok := settings.Value(http2.SettingInitialWindowSize)
		if !ok {
			return
		}
		value <- v
		<-stop
	}()
	t.Cleanup(func() { close(stop); listener.Close(); <-done })
	return listener.Addr().(*net.TCPAddr).Port, value
}

func windowNode(up, down int, w any) map[string]any {
	d := map[string]any{"server": "127.0.0.1", "port": down, "alpn": []string{"h2"}}
	if w != nil {
		d["h2-stream-receive-window"] = w
	}
	return map[string]any{"name": "loopback", "type": "vless", "server": "127.0.0.1", "port": up,
		"uuid": "00000000-0000-4000-8000-000000000001", "network": "xhttp", "alpn": []string{"h2"},
		"xhttp-opts": map[string]any{"mode": "stream-up", "download-settings": d}}
}

func TestXHTTPDownstreamReceiveWindow(t *testing.T) {
	for _, tc := range []struct {
		name       string
		configured any
		want       uint32
	}{
		{"omitted", nil, 4 << 20}, {"zero", 0, 4 << 20}, {"custom", 16 << 20, 16 << 20}, {"other", 2 << 20, 2 << 20},
	} {
		tc := tc
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			up, u := windowPeer(t)
			down, d := windowPeer(t)
			p, err := ParseProxy(windowNode(up, down, tc.configured))
			if err != nil {
				t.Fatal(err)
			}
			defer p.Close()
			ctx, cancel := context.WithTimeout(context.Background(), 8*time.Second)
			defer cancel()
			conn, err := p.DialContext(ctx, &C.Metadata{Host: "test.invalid", DstPort: 443})
			if err != nil {
				t.Fatal(err)
			}
			defer conn.Close()
			for _, side := range []struct {
				name string
				seen <-chan uint32
				want uint32
			}{{"upload", u, 4 << 20}, {"download", d, tc.want}} {
				select {
				case v, ok := <-side.seen:
					if !ok || v != side.want {
						t.Fatalf("%s SETTINGS: got %d (present %t), want %d", side.name, v, ok, side.want)
					}
				case <-ctx.Done():
					t.Fatalf("%s: %v", side.name, ctx.Err())
				}
			}
		})
	}
}

func TestXHTTPDownstreamReceiveWindowBounds(t *testing.T) {
	for _, w := range []int64{-1, 1 << 31, 1 << 40} {
		_, err := ParseProxy(windowNode(1, 2, w))
		if err == nil || !strings.Contains(err.Error(), "h2-stream-receive-window") {
			t.Fatalf("window %d: %v", w, err)
		}
	}
	for _, proto := range []string{"http/1.1", "h2", "h3"} {
		m := windowNode(1, 2, int64(1<<31-1))
		d := m["xhttp-opts"].(map[string]any)["download-settings"].(map[string]any)
		d["alpn"] = []string{proto}
		if proto == "h3" {
			d["tls"] = true
		}
		p, err := ParseProxy(m)
		if err != nil {
			t.Fatalf("%s: %v", proto, err)
		}
		p.Close()
	}
}
