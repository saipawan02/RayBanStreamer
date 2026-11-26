#!/usr/bin/env python3
"""
Phase 2 Task 4: Android-to-Relay Data Integrity Validator

Compares android_video_manifest.jsonl and relay_video_manifest.jsonl
to verify no corruption, loss, duplication, or reordering occurred.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Set


def load_manifest(path: Path) -> List[Dict]:
    """Load JSONL manifest file"""
    entries = []
    with open(path, 'r') as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                entries.append(entry)
            except json.JSONDecodeError as e:
                print(f"WARNING: Failed to parse line {line_num}: {e}", file=sys.stderr)
    return entries


def validate_integrity(android_path: Path, relay_path: Path) -> bool:
    """
    Compare Android and relay manifests.

    Returns True if integrity check PASSED, False otherwise.
    """

    print("=" * 70)
    print("PHASE 2 TASK 4: DATA INTEGRITY VALIDATION")
    print("=" * 70)
    print()

    # Load manifests
    print(f"Loading Android manifest: {android_path}")
    android_frames = load_manifest(android_path)
    print(f"  → {len(android_frames)} frames")

    print(f"Loading Relay manifest: {relay_path}")
    relay_frames = load_manifest(relay_path)
    print(f"  → {len(relay_frames)} frames")
    print()

    # Create lookup dictionaries by sequence number
    android_by_seq = {f['sequence']: f for f in android_frames}
    relay_by_seq = {f['sequence']: f for f in relay_frames}

    android_sequences = set(android_by_seq.keys())
    relay_sequences = set(relay_by_seq.keys())

    # Check for missing frames
    missing = android_sequences - relay_sequences
    # Check for unexpected frames (relay has but android doesn't)
    unexpected = relay_sequences - android_sequences

    # Check for duplicates (same sequence appears multiple times)
    android_seq_counts = {}
    for f in android_frames:
        seq = f['sequence']
        android_seq_counts[seq] = android_seq_counts.get(seq, 0) + 1

    relay_seq_counts = {}
    for f in relay_frames:
        seq = f['sequence']
        relay_seq_counts[seq] = relay_seq_counts.get(seq, 0) + 1

    android_duplicates = {seq: count for seq, count in android_seq_counts.items() if count > 1}
    relay_duplicates = {seq: count for seq, count in relay_seq_counts.items() if count > 1}

    # Check for out-of-order frames
    # Frames are out of order if sequence numbers are not monotonically increasing
    android_out_of_order = []
    for i in range(1, len(android_frames)):
        if android_frames[i]['sequence'] < android_frames[i-1]['sequence']:
            android_out_of_order.append((android_frames[i-1]['sequence'], android_frames[i]['sequence']))

    relay_out_of_order = []
    for i in range(1, len(relay_frames)):
        if relay_frames[i]['sequence'] < relay_frames[i-1]['sequence']:
            relay_out_of_order.append((relay_frames[i-1]['sequence'], relay_frames[i]['sequence']))

    # Check for size and checksum mismatches in common frames
    size_mismatches = []
    checksum_mismatches = []

    common_sequences = android_sequences & relay_sequences
    for seq in sorted(common_sequences):
        android_frame = android_by_seq[seq]
        relay_frame = relay_by_seq[seq]

        if android_frame['size'] != relay_frame['size']:
            size_mismatches.append({
                'sequence': seq,
                'android_size': android_frame['size'],
                'relay_size': relay_frame['size']
            })

        if android_frame['sha256'] != relay_frame['sha256']:
            checksum_mismatches.append({
                'sequence': seq,
                'android_sha256': android_frame['sha256'],
                'relay_sha256': relay_frame['sha256']
            })

    # Print results
    print("=" * 70)
    print("INTEGRITY VALIDATION RESULTS")
    print("=" * 70)
    print()

    print(f"ANDROID FRAMES SENT:       {len(android_frames)}")
    print(f"RELAY FRAMES RECEIVED:     {len(relay_frames)}")
    print(f"MISSING FRAMES:            {len(missing)}")
    print(f"UNEXPECTED FRAMES:         {len(unexpected)}")
    print(f"DUPLICATE FRAMES (Android):{len(android_duplicates)}")
    print(f"DUPLICATE FRAMES (Relay):  {len(relay_duplicates)}")
    print(f"OUT-OF-ORDER (Android):    {len(android_out_of_order)}")
    print(f"OUT-OF-ORDER (Relay):      {len(relay_out_of_order)}")
    print(f"SIZE MISMATCHES:           {len(size_mismatches)}")
    print(f"CHECKSUM MISMATCHES:       {len(checksum_mismatches)}")
    print()

    # Determine pass/fail
    passed = (
        len(missing) == 0 and
        len(unexpected) == 0 and
        len(android_duplicates) == 0 and
        len(relay_duplicates) == 0 and
        len(android_out_of_order) == 0 and
        len(relay_out_of_order) == 0 and
        len(size_mismatches) == 0 and
        len(checksum_mismatches) == 0
    )

    if passed:
        print("INTEGRITY RESULT:          ✅ PASS")
        print()
        print("All frames transmitted successfully with:")
        print("  - Identical sequence numbers")
        print("  - Identical payload lengths")
        print("  - Identical SHA-256 checksums")
        print()
    else:
        print("INTEGRITY RESULT:          ❌ FAIL")
        print()

        if missing:
            print(f"Missing frames (sent but not received): {sorted(missing)[:10]}")
            if len(missing) > 10:
                print(f"  ... and {len(missing) - 10} more")
            print()

        if unexpected:
            print(f"Unexpected frames (received but not sent): {sorted(unexpected)[:10]}")
            if len(unexpected) > 10:
                print(f"  ... and {len(unexpected) - 10} more")
            print()

        if android_duplicates:
            print(f"Android duplicate sequences: {android_duplicates}")
            print()

        if relay_duplicates:
            print(f"Relay duplicate sequences: {relay_duplicates}")
            print()

        if android_out_of_order:
            print(f"Android out-of-order: {android_out_of_order[:10]}")
            if len(android_out_of_order) > 10:
                print(f"  ... and {len(android_out_of_order) - 10} more")
            print()

        if relay_out_of_order:
            print(f"Relay out-of-order: {relay_out_of_order[:10]}")
            if len(relay_out_of_order) > 10:
                print(f"  ... and {len(relay_out_of_order) - 10} more")
            print()

        if size_mismatches:
            print("Size mismatches (first 10):")
            for mismatch in size_mismatches[:10]:
                print(f"  Seq {mismatch['sequence']}: Android={mismatch['android_size']}, Relay={mismatch['relay_size']}")
            if len(size_mismatches) > 10:
                print(f"  ... and {len(size_mismatches) - 10} more")
            print()

        if checksum_mismatches:
            print("Checksum mismatches (first 10):")
            for mismatch in checksum_mismatches[:10]:
                print(f"  Seq {mismatch['sequence']}:")
                print(f"    Android: {mismatch['android_sha256']}")
                print(f"    Relay:   {mismatch['relay_sha256']}")
            if len(checksum_mismatches) > 10:
                print(f"  ... and {len(checksum_mismatches) - 10} more")
            print()

    print("=" * 70)

    return passed


def main():
    parser = argparse.ArgumentParser(
        description="Validate Android-to-Relay data integrity"
    )
    parser.add_argument(
        "android_manifest",
        type=Path,
        help="Path to android_video_manifest.jsonl"
    )
    parser.add_argument(
        "relay_manifest",
        type=Path,
        help="Path to relay_video_manifest_<session>.jsonl"
    )

    args = parser.parse_args()

    if not args.android_manifest.exists():
        print(f"ERROR: Android manifest not found: {args.android_manifest}", file=sys.stderr)
        return 1

    if not args.relay_manifest.exists():
        print(f"ERROR: Relay manifest not found: {args.relay_manifest}", file=sys.stderr)
        return 1

    passed = validate_integrity(args.android_manifest, args.relay_manifest)

    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
