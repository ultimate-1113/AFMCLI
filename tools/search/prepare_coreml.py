#!/usr/bin/env python3
"""Prepare the optional fixed-document convolution for afm-search throughput."""
import argparse
import base64
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path


RECEIPT = "afm-search-build.json"


def bundle_files(directory):
    files = {}
    total = 0
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise ValueError("unexpected link in compiled index")
        if path.is_dir():
            continue
        name = path.relative_to(directory).as_posix()
        if name == RECEIPT:
            continue
        if not path.is_file():
            raise ValueError("unexpected entry in compiled index")
        total += path.stat().st_size
        if len(files) >= 1024 or total > 256 << 20:
            raise ValueError("compiled index exceeds size limit")
        h = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1 << 20), b""):
                h.update(chunk)
        files[name] = h.hexdigest()
    if not files:
        raise ValueError("empty compiled index")
    return files


def intact(directory, fingerprint):
    receipt = directory / RECEIPT
    try:
        if directory.is_symlink() or receipt.is_symlink() or not receipt.is_file() or receipt.stat().st_size > 1 << 20:
            return False
        return json.loads(receipt.read_bytes()) == {
            "version": 1, "index_sha256": fingerprint, "files": bundle_files(directory)
        }
    except (ValueError, OSError):
        return False


def existing(directory, fingerprint):
    if not intact(directory, fingerprint):
        raise ValueError("Existing compiled index failed integrity verification. Stop its search process, move " +
                         str(directory) + " to a backup name, and run the builder again.")
    return directory


def prepare(directory):
    index_file = directory / "afm-search-index.json"
    with index_file.open("rb") as source:
        raw = source.read((128 << 20) + 1)
    if len(raw) > 128 << 20:
        raise ValueError("index exceeds 128 MiB")
    fingerprint = hashlib.sha256(raw).hexdigest()
    disk = json.loads(raw)
    rows = len(disk["ids"])
    if disk["version"] != 1 or not 4096 <= rows <= 16384:
        raise ValueError("requires a version 1 index with 4096 to 16384 documents")
    destination = directory / ("search-" + fingerprint + ".mlmodelc")
    if destination.exists() or destination.is_symlink():
        return existing(destination, fingerprint)
    bits = base64.b64decode(disk["bits"], validate=True)
    if len(bits) != rows * 128:
        raise ValueError("unexpected document matrix size")
    import coremltools as ct
    import numpy as np
    from coremltools.converters.mil.mil import Builder as mb, types

    packed = np.frombuffer(bits, dtype=np.uint8).reshape(rows, 128)
    weights = np.unpackbits(packed, axis=1, bitorder="little").astype(np.float16).reshape(rows, 1024, 1, 1)

    @mb.program(input_specs=[mb.TensorSpec(shape=(1, 1024, 1, 1024), dtype=types.fp16)],
                opset_version=ct.target.macOS13)
    def graph(query):
        return mb.conv(x=query, weight=weights, name="scores")

    with tempfile.TemporaryDirectory(prefix=".afm-search-", dir=directory) as temp:
        package = Path(temp) / "search.mlpackage"
        built = Path(temp) / "search.mlmodelc"
        model = ct.convert(graph, source="milinternal", convert_to="mlprogram", skip_model_load=True,
                           minimum_deployment_target=ct.target.macOS13, compute_precision=ct.precision.FLOAT16)
        model.user_defined_metadata["afmcli_search_index_sha256"] = fingerprint
        model.save(str(package))
        ct.models.utils.compile_model(str(package), destination_path=str(built))
        receipt = {"version": 1, "index_sha256": fingerprint, "files": bundle_files(built)}
        (built / RECEIPT).write_text(json.dumps(receipt, sort_keys=True) + "\n")
        if hashlib.sha256(index_file.read_bytes()).hexdigest() != fingerprint:
            raise ValueError("document index changed while preparing; run preparation again")
        try:
            os.rename(built, destination)
        except OSError:
            if not destination.exists():
                raise
            return existing(destination, fingerprint)
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("index", type=Path)
    try:
        print(prepare(parser.parse_args().index))
    except (ValueError, OSError, ImportError, KeyError, RuntimeError) as exc:
        sys.exit(str(exc))
