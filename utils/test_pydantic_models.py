import argparse
import importlib
import sys
from pathlib import Path


def main():
    sys.path.append(str(Path(__file__).resolve().parent.parent))

    parser = argparse.ArgumentParser(
        description="Verify you can load the JSON payloads returned from the NE into the specified Pydantic model"
    )
    parser.add_argument(
        "--module", help="Module path (default is 'temp.models')", default="temp.models"
    )
    parser.add_argument(
        "--model", help="Name of the model to import (default is 'Ne')", default="Ne"
    )
    parser.add_argument(
        "--json",
        help="file .json containing the payload (default is 'temp/instance.json')",
        default="temp/instance.json",
    )

    args = parser.parse_args()

    module = importlib.import_module(args.module)

    try:
        Model = getattr(module, args.model)
    except AttributeError:
        raise ValueError(f"model '{args.model}' not found in module '{args.module}'")

    payload = Path("temp/instance.json").read_text(encoding="utf-8")
    instance = Model.model_validate_json(payload)
    print(f"{instance.model_dump_json(indent=2, by_alias=True, exclude_unset=True)}")
    print("All good!")


if __name__ == "__main__":
    main()
