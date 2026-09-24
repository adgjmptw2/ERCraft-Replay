// erdecode is a standalone .er reader. No Python runtime is invoked.
package main

import (
	"bufio"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"path/filepath"

	"github.com/adgjmptw2/ERCraft-Replay/go-replay/replay"
)

func run() int {
	input := flag.String("input", "", "source .er file (never modified)")
	schemas := flag.String("schema-dir", "../core/schema", "exact versioned schema directory")
	output := flag.String("out", "", "optional decoded NDJSON output; must not already exist")
	raw := flag.Bool("raw-records", false, "retain all compressed record bytes as well as packet bytes")
	allowGaps := flag.Bool("allow-gaps", false, "exit 0 on decode gaps (the summary still marks complete=false)")
	limit := flag.Int("max-payload", replay.DefaultMaxPayload, "maximum compressed/decompressed bytes per record")
	flag.Parse()
	if *input == "" {
		fmt.Fprintln(os.Stderr, "-input is required")
		return 1
	}
	var out *os.File
	var buffer *bufio.Writer
	var encoder *json.Encoder
	var temp string
	if *output != "" {
		if _, err := os.Lstat(*output); err == nil || !os.IsNotExist(err) {
			fmt.Fprintln(os.Stderr, "output exists or cannot be checked")
			return 1
		}
		var err error
		out, err = os.CreateTemp(filepath.Dir(*output), ".erdecode-*.partial")
		if err != nil {
			fmt.Fprintln(os.Stderr, err)
			return 1
		}
		temp = out.Name()
		defer func() { out.Close(); os.Remove(temp) }()
		buffer = bufio.NewWriterSize(out, 1<<20)
		encoder = json.NewEncoder(buffer)
		encoder.SetEscapeHTML(false)
	}
	write := func(value any) error {
		if encoder == nil {
			return nil
		}
		return encoder.Encode(value)
	}
	summary, err := replay.DecodeFile(*input, replay.Options{SchemaDirectory: *schemas, MaxPayload: *limit, KeepRawRecords: *raw},
		func(h replay.Header) error {
			return write(struct {
				Type   string        `json:"type"`
				Header replay.Header `json:"header"`
			}{"header", h})
		},
		func(r replay.Record) error {
			return write(struct {
				Type   string        `json:"type"`
				Record replay.Record `json:"record"`
			}{"record", r})
		})
	if err != nil {
		json.NewEncoder(os.Stdout).Encode(summary)
		fmt.Fprintln(os.Stderr, err)
		return 1
	}
	if encoder != nil {
		err = write(struct {
			Type    string         `json:"type"`
			Summary replay.Summary `json:"summary"`
		}{"summary", summary})
		if err == nil {
			err = buffer.Flush()
		}
		if err == nil {
			err = out.Sync()
		}
		if closeErr := out.Close(); err == nil {
			err = closeErr
		}
		// Atomic no-replace publication prevents overwriting an existing result/source.
		if err == nil {
			err = os.Link(temp, *output)
		}
		if err != nil {
			fmt.Fprintln(os.Stderr, err)
			return 1
		}
	}
	if err = json.NewEncoder(os.Stdout).Encode(summary); err != nil {
		fmt.Fprintln(os.Stderr, err)
		return 1
	}
	if !summary.Complete && !*allowGaps {
		return 2
	}
	return 0
}
func main() { os.Exit(run()) }
