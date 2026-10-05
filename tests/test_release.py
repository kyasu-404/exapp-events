from xml.etree.ElementTree import parse

import pytest

from scripts.prepare_release import prepare


def test_release_metadata_keeps_identity_and_routes(tmp_path):
    output = tmp_path / "info.xml"
    assert (
        prepare("ghcr.io", "example-owner/exapp-events", "0.1.0", output)
        == "ghcr.io/example-owner/exapp-events:0.1.0"
    )
    tree = parse(output)
    assert tree.findtext("id") == "exapp_events" and tree.findtext("name") == "Мероприятия"
    assert tree.findtext("external-app/docker-install/image") == "example-owner/exapp-events"
    assert tree.findtext("external-app/routes/route/access_level") == "ADMIN"


@pytest.mark.parametrize(
    "registry,image,tag",
    [
        ("https://ghcr.io", "owner/app", "1"),
        ("ghcr.io", "exapp-events", "1"),
        ("ghcr.io", "owner/app", "bad tag"),
    ],
)
def test_release_rejects_unusable_image_reference(tmp_path, registry, image, tag):
    with pytest.raises(ValueError):
        prepare(registry, image, tag, tmp_path / "info.xml")
