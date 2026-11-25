"""Package a tested binary, runnable examples, and the locked dependency and Rust notices."""
import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import posixpath
import re
import tarfile
from urllib.parse import quote, urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[1]
PROJECT = "https://github.com/ultimate-1113/AFMCLI"
LOCAL_BINARY_PATH = re.compile(rb"/(?:users|home|volumes|(?:private/)?tmp|(?:private/)?var/(?:folders|tmp))/", re.I)
PROJECT_FILES = (
    "LICENSE", "NOTICES.md", "README.md", "examples/guarded-row.json",
    "eval/procedures/README.md", "skills/short-evidence/SKILL.md", "skills/linear-declarations/SKILL.md",
    "eval/calculator/README.md", "eval/calculator/native/CalculatorProbe.swift",
    "examples/file_tasks/demo.py", "examples/file_tasks/README.md",
)
SAMPLE_FILES = ("notice.txt", "order.txt", "before.json", "after.json", "before.csv", "after.csv",
                "csv-input.json", "result.json")

START = """AFMCLI を展開したフォルダーで実行してください。

式が決まっているときは、Rust の電卓がローカルで計算します。

    ./afmcli --text calc '18 * 275 + 320'

出力は 5270 です。JSON で式・値・近似の有無・角度単位を確認する例です。

    ./afmcli calc --angle deg 'sin(30)'
    ./afmcli calc --help

文章から式を提案させるには、Apple Intelligence が有効な Mac で
doctor の available と target を確認してから、問題を標準入力へ渡します。

    ./afmcli doctor
    printf '%s\\n' 'ノート18冊を1冊275円で買い、送料320円を1回払います。合計は何円ですか。' | ./afmcli calc --from-text

status が review のときは、result.expression の数値と演算が問題の条件に合うか確認してください。
情報不足などで保留になったときは notes に理由が入ります。

TXT・JSON・CSVの合成例は、Python 3 で新規または空のフォルダーへ書き出せます。

    python3 examples/file_tasks/demo.py --binary ./afmcli --out ./demo-files

同じ例の入出力は examples/file_tasks/sample/ にあります。Swift で AFM に電卓を登録する手順は
eval/calculator/native/README.md、コマンド一覧は ./afmcli --help で確認できます。

ソースとインストール手順
https://github.com/ultimate-1113/AFMCLI
"""

SWIFT_GUIDE = """# Swift から AFM に関数電卓を登録する

Apple Silicon、macOS 27 以降、その macOS の Foundation Models に対応する Swift 6 と SDK が必要です。
展開したフォルダーでサンプルをビルドします。

```sh
xcrun swiftc -parse-as-library -swift-version 6 -O -target arm64-apple-macosx27.0 \\
  eval/calculator/native/CalculatorProbe.swift -o ./calculator-probe
```

電卓のハンドラだけを試す例です。出力の `source` が `offline-check`、`value` が `5270` になります。

```sh
printf '%s\\n' '{"expression":"18*275+320"}' | ./calculator-probe check-calculator "$PWD/afmcli" deg
```

次は `doctor` の `available` が true、`variant` が `AFM 3 Core Advanced` の Mac で実行します。
`registered` は新しいセッションに電卓を登録します。モデルが電卓を呼ぶと、`event: callback` に渡した式と値が出ます。
`event: final` の `answer` がモデルの最終回答です。

```sh
./calculator-probe doctor
printf '%s\\n' '電卓を使って (17+8)*6 を計算し、数値だけで答えてください。' | ./calculator-probe registered "$PWD/afmcli" deg
```

[Apple のツール呼び出し仕様](https://developer.apple.com/documentation/foundationmodels/expanding-generation-with-tool-calling)・[試験条件と再現用ソース]({source})
"""


def release_links(name, data, members, ref):
    """Keep packaged links local and send source-only references to the matching tag."""
    def link(match):
        uri = urlsplit(match.group(0))
        if uri.scheme or uri.netloc or not uri.path or uri.path.startswith("/"):
            return match.group(0)
        target = posixpath.normpath(posixpath.join(posixpath.dirname(name), uri.path))
        if target in members or any(path.startswith(target + "/") for path in members):
            return match.group(0)
        local = ROOT / target
        if not local.exists() or target.startswith("../"):
            return match.group(0)
        kind = "tree" if local.is_dir() else "blob"
        url = urlsplit(f"{PROJECT}/{kind}/{ref}/{quote(target)}")
        return urlunsplit((url.scheme, url.netloc, url.path, uri.query, uri.fragment))
    text = data.decode("utf-8")
    return re.sub(r"(?<=\]\()[^()\s]+(?=\))", link, text).encode("utf-8")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("binary", type=Path)
    p.add_argument("metadata", type=Path, help="cargo metadata --locked --format-version 1 --filter-platform aarch64-apple-darwin")
    p.add_argument("rust_docs", type=Path, help="installed Rust share/doc/rust directory")
    p.add_argument("--rust-license-dir", type=Path, help="Rust distribution directory containing LICENSE-MIT and LICENSE-APACHE")
    p.add_argument("archive", type=Path)
    a = p.parse_args()
    binary = a.binary.read_bytes()
    if LOCAL_BINARY_PATH.search(binary):
        p.error("release executable contains an absolute local filesystem path")
    packages = json.loads(a.metadata.read_text(encoding="utf-8"))["packages"]
    ref = next(("v" + crate["version"] for crate in packages if not crate["source"] and crate["name"] == "afmcli"), "main")
    members = [*PROJECT_FILES, *("examples/file_tasks/sample/" + name for name in SAMPLE_FILES)]
    files = {name: (ROOT / name).read_bytes() for name in members}
    files["afmcli"] = binary
    files["START.txt"] = START.encode()
    files["eval/calculator/native/README.md"] = SWIFT_GUIDE.replace(
        "{source}", f"{PROJECT}/blob/{ref}/eval/calculator/native/README.md").encode()
    files["examples/file_tasks/README.md"] = files["examples/file_tasks/README.md"].replace(
        b"--binary ./target/release/afmcli", b"--binary ./afmcli")
    for name in PROJECT_FILES:
        if name.endswith(".md"):
            files[name] = release_links(name, files[name], files, ref)
    for crate in packages:
        if not crate["source"]:
            continue
        directory = Path(crate["manifest_path"]).parent
        licenses = [f for f in sorted(directory.rglob("*"))
                    if f.is_file() and f.name.lower().startswith(("license", "licence", "notice", "copyright", "copying", "unlicense"))]
        if not licenses:
            raise ValueError("missing license for " + crate["name"])
        for f in licenses:
            key = f'licenses/{crate["name"]}-{crate["version"]}/{f.relative_to(directory).as_posix()}'
            files[key] = f.read_bytes()
    for name in ("COPYRIGHT-library.html", "LICENSE-MIT", "LICENSE-APACHE"):
        directory = a.rust_docs if name.endswith(".html") else a.rust_license_dir or a.rust_docs
        files["licenses/rust/" + name] = (directory / name).read_bytes()
    a.archive.parent.mkdir(parents=True, exist_ok=True)
    with a.archive.open("wb") as output, gzip.GzipFile(fileobj=output, filename="", mode="wb", mtime=0) as gz, tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for name, data in sorted(files.items()):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o755 if name == "afmcli" else 0o644
            tar.addfile(info, io.BytesIO(data))
    checksum = hashlib.sha256(a.archive.read_bytes()).hexdigest()
    a.archive.with_suffix(a.archive.suffix + ".sha256").write_text(f"{checksum}  {a.archive.name}\n")
    print(len(files), "members;", checksum)


if __name__ == "__main__":
    main()
