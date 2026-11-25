"""Package a prebuilt, tested binary with locked dependency and Rust runtime notices."""
import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import tarfile

ROOT=Path(__file__).resolve().parents[1]

def main():
    p=argparse.ArgumentParser()
    p.add_argument("binary",type=Path)
    p.add_argument("metadata",type=Path,help="cargo metadata --locked --format-version 1 --filter-platform aarch64-apple-darwin")
    p.add_argument("rust_docs",type=Path,help="installed Rust share/doc/rust directory")
    p.add_argument("--rust-license-dir",type=Path,help="Rust distribution directory containing LICENSE-MIT and LICENSE-APACHE")
    p.add_argument("archive",type=Path)
    a=p.parse_args()
    files={"afmcli":a.binary.read_bytes(),"LICENSE":(ROOT/"LICENSE").read_bytes(),"NOTICES.md":(ROOT/"NOTICES.md").read_bytes()}
    for name in ("README.md", "examples/guarded-row.json", "eval/procedures/README.md", "skills/short-evidence/SKILL.md",
                 "skills/linear-declarations/SKILL.md"):
        files[name]=(ROOT/name).read_bytes()
    files["START.txt"]=b"Run ./afmcli doctor, then ./afmcli --help. Source and installation instructions: https://github.com/ultimate-1113/AFMCLI\n"
    for crate in json.loads(a.metadata.read_text())["packages"]:
        if not crate["source"]:continue
        d=Path(crate["manifest_path"]).parent
        licenses=[f for f in sorted(d.rglob("*")) if f.is_file() and f.name.lower().startswith(("license","licence","notice","copyright","copying","unlicense"))]
        if not licenses:raise ValueError("missing license for "+crate["name"])
        for f in licenses:
            key=f'licenses/{crate["name"]}-{crate["version"]}/{f.relative_to(d).as_posix()}'
            files[key]=f.read_bytes()
    for name in ("COPYRIGHT-library.html","LICENSE-MIT","LICENSE-APACHE"):
        directory=a.rust_docs if name.endswith(".html") else a.rust_license_dir or a.rust_docs
        files["licenses/rust/"+name]=(directory/name).read_bytes()
    a.archive.parent.mkdir(parents=True,exist_ok=True)
    with a.archive.open("wb") as output, gzip.GzipFile(fileobj=output,filename="",mode="wb",mtime=0) as gz, tarfile.open(fileobj=gz,mode="w",format=tarfile.PAX_FORMAT) as tar:
        for name,data in sorted(files.items()):
            info=tarfile.TarInfo(name);info.size=len(data);info.mode=0o755 if name=="afmcli" else 0o644
            tar.addfile(info,io.BytesIO(data))
    checksum=hashlib.sha256(a.archive.read_bytes()).hexdigest()
    a.archive.with_suffix(a.archive.suffix+".sha256").write_text(f"{checksum}  {a.archive.name}\n")
    print(len(files),"members;",checksum)
if __name__=="__main__":main()
