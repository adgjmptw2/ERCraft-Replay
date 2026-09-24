package replay

import (
	"os"
	"testing"
)

// Local replay is opt-in and never copied into test fixtures or modified.
func BenchmarkDecodeFile(b *testing.B) {
	path := os.Getenv("ER_GO_REPLAY")
	if path == "" {
		b.Skip("ER_GO_REPLAY not set")
	}
	info, err := os.Stat(path)
	if err != nil {
		b.Fatal(err)
	}
	b.SetBytes(info.Size())
	b.ReportAllocs()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		summary, err := DecodeFile(path, Options{SchemaDirectory: "../../core/schema"}, nil, nil)
		if err != nil || !summary.Complete || summary.PacketCount == 0 {
			b.Fatalf("decode failed: %v %+v", err, summary)
		}
	}
}
