#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "ncclient>=0.7.0",
# ]
# ///
import os
from ncclient import manager
from lxml import etree


class YangDownloader:
    """Analytical approach to stripping a device of its YANG models."""

    def __init__(self, host, port, user, password, output_dir="yang_models"):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.output_dir = output_dir

        if not os.path.exists(self.output_dir):
            os.makedirs(self.output_dir)

    def get_schema_list(self, netconf_manager):
        """Methodically retrieves the list of all supported schemas."""
        filter_exp = """
        <netconf-state xmlns="urn:ietf:params:xml:ns:yang:ietf-netconf-monitoring">
            <schemas/>
        </netconf-state>
        """
        response = netconf_manager.get(filter=("subtree", filter_exp))
        root = etree.fromstring(response.xml.encode())
        namespaces = {"mon": "urn:ietf:params:xml:ns:yang:ietf-netconf-monitoring"}
        return root.xpath("//mon:schema", namespaces=namespaces)

    def download_all(self):
        """Iterates and executes the get-schema operation for every identified model."""
        try:
            with manager.connect(
                host=self.host,
                port=self.port,
                username=self.user,
                password=self.password,
                hostkey_verify=False,
            ) as m:
                schemas = self.get_schema_list(m)
                print(f"[*] Found {len(schemas)} schemas. Starting extraction...")

                for schema in schemas:
                    name = schema.find(
                        "{urn:ietf:params:xml:ns:yang:ietf-netconf-monitoring}identifier"
                    ).text
                    version = schema.find(
                        "{urn:ietf:params:xml:ns:yang:ietf-netconf-monitoring}version"
                    ).text

                    filename = f"{name}@{version}.yang" if version else f"{name}.yang"
                    filepath = os.path.join(self.output_dir, filename)

                    try:
                        content = m.get_schema(identifier=name, version=version).data
                        with open(filepath, "w", encoding="utf-8") as f:
                            f.write(content)
                        print(f"[+] Saved: {filename}")
                    except Exception as e:
                        print(f"[!] Failed to fetch {name}: {e}")

        except Exception as e:
            print(f"CRITICAL SYSTEM ERROR: {e}")


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Download YANG models from a network element"
    )
    parser.add_argument("ip", help="IP of the network element")
    parser.add_argument("username", help="Username")
    parser.add_argument("password", help="Password")
    parser.add_argument("-p", "--port", help="port number (default: 830)", default=830)
    parser.add_argument(
        "-o",
        "--output-dir",
        help="save .yang files inside this dir (default: ./yang_models)",
        default="yang_models",
    )

    args = parser.parse_args()
    extractor = YangDownloader(
        host=args.ip,
        port=args.port,
        user=args.username,
        password=args.password,
        output_dir=args.output_dir,
    )
    extractor.download_all()


if __name__ == "__main__":
    main()
