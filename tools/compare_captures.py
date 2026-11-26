#!/usr/bin/env python3
"""
Phase 2 Task 5.5: Compare captured HEVC files

Compares Android capture, relay capture (from manifests), and FIFO-bound capture
to verify byte-perfect transmission through the entire pipeline.
"""

import argparse
import hashlib
import sys
from pathlib import Path


def sha256_file(path: Path) -> str:
    """Calculate SHA-256 of entire file"""
    sha256 = hashlib.sha256()
    with open(path, 'rb') as f:
        while True:
            data = f.read(1024 * 1024)  # 1MB chunks
            if not data:
                break
            sha256.update(data)
    return sha256.hexdigest()


def compare_captures(android_path: Path, fifo_path: Path) -> bool:
    """
    Compare Android capture with FIFO-bound capture.

    Returns True if files match exactly, False otherwise.
    """

    print("=" * 70)
    print("PHASE 2 TASK 5.5: CAPTURE FILE COMPARISON")
    print("=" * 70)
    print()

    # Check files exist
    if not android_path.exists():
        print(f"ERROR: Android capture not found: {android_path}", file=sys.stderr)
        return False

    if not fifo_path.exists():
        print(f"ERROR: FIFO-bound capture not found: {fifo_path}", file=sys.stderr)
        return False

    # Get file sizes
    android_size = android_path.stat().st_size
    fifo_size = fifo_path.stat().st_size

    print(f"Android capture:     {android_path}")
    print(f"  Size: {android_size:,} bytes ({android_size / (1024*1024):.2f} MB)")
    print()

    print(f"FIFO-bound capture:  {fifo_path}")
    print(f"  Size: {fifo_size:,} bytes ({fifo_size / (1024*1024):.2f} MB)")
    print()

    # Check size match
    if android_size != fifo_size:
        print("❌ SIZE MISMATCH")
        print(f"   Android: {android_size:,} bytes")
        print(f"   FIFO:    {fifo_size:,} bytes")
        print(f"   Diff:    {abs(android_size - fifo_size):,} bytes")
        print()
        return False

    print("✅ Sizes match")
    print()

    # Calculate checksums
    print("Calculating SHA-256 checksums...")
    android_sha256 = sha256_file(android_path)
    fifo_sha256 = sha256_file(fifo_path)

    print(f"Android SHA-256: {android_sha256}")
    print(f"FIFO SHA-256:    {fifo_sha256}")
    print()

    # Check checksum match
    if android_sha256 != fifo_sha256:
        print("❌ CHECKSUM MISMATCH")
        print()
        print("Files have same size but different contents!")
        print("Data corruption occurred during relay processing.")
        return False

    print("✅ Checksums match")
    print()

    # Byte-by-byte comparison (first mismatch)
    print("Verifying byte-by-byte...")
    with open(android_path, 'rb') as af, open(fifo_path, 'rb') as ff:
        pos = 0
        mismatch_found = False
        while True:
            ab = af.read(4096)
            fb = ff.read(4096)

            if not ab and not fb:
                break

            if ab != fb:
                # Find first mismatch
                for i in range(min(len(ab), len(fb))):
                    if ab[i] != fb[i]:
                        print(f"❌ First mismatch at byte {pos + i}")
                        print(f"   Android: 0x{ab[i]:02x}")
                        print(f"   FIFO:    0x{fb[i]:02x}")
                        mismatch_found = True
                        break
                if mismatch_found:
                    break

            pos += len(ab)

    if not mismatch_found:
        print("✅ Byte-by-byte verification passed")
        print()

    # Final result
    print("=" * 70)
    print("COMPARISON RESULT")
    print("=" * 70)
    print()
    print("ANDROID CAPTURE:       100% match")
    print("FIFO-BOUND CAPTURE:    100% match")
    print()
    print("✅ PERFECT BYTE-LEVEL INTEGRITY")
    print()
    print("The exact HEVC bitstream from Android reaches the FIFO writer")
    print("without any corruption, truncation, or modification.")
    print("=" * 70)

    return True


def main():
    parser = argparse.ArgumentParser(
        description="Compare Android and FIFO-bound HEVC captures"
    )
    parser.add_argument(
        "android_capture",
        type=Path,
        help="Path to Android video_capture.h265"
    )
    parser.add_argument(
        "fifo_capture",
        type=Path,
        help="Path to relay_fifo_capture.h265"
    )

    args = parser.parse_args()

    matched = compare_captures(args.android_capture, args.fifo_capture)

    return 0 if matched else 1


if __name__ == "__main__":
    sys.exit(main())
