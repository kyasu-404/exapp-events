"""Create local installation metadata for an explicitly selected container registry.

Does not build, push or deploy anything. The source template is left unchanged.
"""

import argparse
import re
from pathlib import Path
from xml.etree.ElementTree import indent, parse


def prepare(registry: str, image: str, tag: str, output: Path):
    if not re.fullmatch(r"[a-z0-9][a-z0-9.-]*(?::[0-9]+)?", registry):
        raise ValueError("Registry must be a hostname, optionally with a port")
    if not re.fullmatch(r"[a-z0-9]+(?:[._-][a-z0-9]+)*(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*", image):
        raise ValueError("Image must be a lowercase container image path")
    if registry in {"docker.io", "ghcr.io"} and "/" not in image:
        raise ValueError("Specify your own namespace/image for this registry")
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}", tag):
        raise ValueError("Invalid container tag")
    tree = parse(Path(__file__).resolve().parents[1] / "appinfo/info.xml")
    for name, value in {"registry": registry, "image": image, "image-tag": tag}.items():
        tree.find(f"external-app/docker-install/{name}").text = value
    output.parent.mkdir(parents=True, exist_ok=True)
    indent(tree, space="  ")
    tree.write(output, encoding="utf-8", xml_declaration=True)
    return f"{registry}/{image}:{tag}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--tag", default="0.1.0")
    parser.add_argument("--output", type=Path, default=Path("dist/info.xml"))
    args = parser.parse_args()
    print(prepare(args.registry, args.image, args.tag, args.output))
    print(f"Local metadata: {args.output}")


if __name__ == "__main__":
    main()
