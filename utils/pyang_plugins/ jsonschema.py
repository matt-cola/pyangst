"""
Unified JSON Schema output plugin - RFC 7951 Compliant (Draft 2020-12)
Combines Data and Operations (RPC/Action) schemas into a single output.
"""

from __future__ import print_function
import optparse
import logging
import json
from pyang import plugin


def pyang_plugin_init():
    plugin.register_plugin(JSONSchemaPlugin())


class JSONSchemaPlugin(plugin.PyangPlugin):
    def __init__(self):
        plugin.PyangPlugin.__init__(self, "jsonschema")
        self.multiple_modules = True

    def add_output_format(self, fmts):
        fmts["jsonschema"] = self

    def add_opts(self, optparser):
        optlist = [
            optparse.make_option(
                "--jsonschema-debug",
                dest="schema_debug",
                action="store_true",
                help="Enable debug logging",
            ),
            optparse.make_option(
                "--jsonschema-no-namespaces",
                dest="schema_no_ns",
                action="store_true",
                help="Strip module names from $defs keys (e.g. ipv4-address instead of ietf-inet-types_ipv4-address)",
            ),
            optparse.make_option(
                "--jsonschema-config-only",
                dest="schema_config_only",
                action="store_true",
                help="Only include nodes where config is true (applies to 'data' section only)",
            ),
        ]
        group = optparser.add_option_group("JSON Schema-specific options")
        group.add_options(optlist)

    def setup_fmt(self, ctx):
        ctx.implicit_errors = False

    def emit(self, ctx, modules, fd):
        if ctx.opts.schema_debug:
            logging.basicConfig(level=logging.DEBUG)

        # Shared definitions dictionary prevents duplication between Data and RPCs
        self.definitions = {}

        # 1. Generate Data Schema
        # We apply the config_filter=True if the user requested config-only
        apply_config_filter = getattr(ctx.opts, "schema_config_only", False)
        data_properties = {}
        for module in modules:
            mod_props = produce_children(
                module, self.definitions, ctx.opts, config_filter=apply_config_filter
            )
            data_properties.update(mod_props)

        # 2. Generate Operations Schema
        # We search for RPCs and Actions. We do NOT apply config filters here.
        ops_properties = {}
        for module in modules:
            # 2a. Global RPCs
            if hasattr(module, "i_children"):
                for child in module.i_children:
                    if child.keyword == "rpc":
                        op_name = qualify_name(child)
                        ops_properties[op_name] = self.produce_operation(
                            child, ctx.opts
                        )

            # 2b. Nested Actions
            self.find_actions(module, ops_properties, ctx.opts)

        # 3. Construct Final Root Object
        result = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": "YANG Model Schema",
            "type": "object",
            "properties": {
                "data": {
                    "type": "object",
                    "title": "Data Tree",
                    "description": "The configuration and state data tree.",
                    "properties": data_properties,
                    "additionalProperties": False,
                },
                "operations": {
                    "type": "object",
                    "title": "Operations",
                    "description": "RPCs and Actions.",
                    "properties": ops_properties,
                    "additionalProperties": False,
                },
            },
            "$defs": self.definitions,
        }

        # Clean up empty definitions
        if not result["$defs"]:
            del result["$defs"]

        fd.write(json.dumps(result, indent=2))

    def find_actions(self, stmt, operations, opts):
        """Recursively find 'action' statements in the data tree."""
        if hasattr(stmt, "i_children"):
            for child in stmt.i_children:
                if child.keyword == "action":
                    op_name = qualify_name(child)
                    operations[op_name] = self.produce_operation(child, opts)
                self.find_actions(child, operations, opts)

    def produce_operation(self, stmt, opts):
        """Generates schema for an RPC or Action (input and output)."""
        op_schema = {
            "type": "object",
            "description": (
                stmt.search_one("description").arg
                if stmt.search_one("description")
                else ""
            ),
            "properties": {},
            "additionalProperties": False,
        }

        input_node = stmt.search_one("input")
        output_node = stmt.search_one("output")

        # config_filter is always False for RPC IO
        if input_node:
            op_schema["properties"]["input"] = {
                "type": "object",
                "properties": produce_children(
                    input_node, self.definitions, opts, config_filter=False
                ),
                "additionalProperties": False,
            }

        if output_node:
            op_schema["properties"]["output"] = {
                "type": "object",
                "properties": produce_children(
                    output_node, self.definitions, opts, config_filter=False
                ),
                "additionalProperties": False,
            }

        return annotate_schema(stmt, op_schema)


# --- Core Logic (Unified) ---


def produce_children(stmt, defs, opts, config_filter=False):
    """
    Generates properties for child nodes.
    config_filter: If True, excludes read-only (non-config) nodes.
    """
    props = {}
    if hasattr(stmt, "i_children"):
        for child in stmt.i_children:
            # Filtering Logic
            if config_filter:
                if hasattr(child, "i_config") and child.i_config is False:
                    continue

            if child.keyword in producers:
                member_name = qualify_name(child)
                # Pass the config_filter down recursively
                schema = producers[child.keyword](child, defs, opts, config_filter)
                if schema:
                    props[member_name] = schema
    return props


def qualify_name(stmt):
    # Logic covers both Data (parent module check) and RPCs (always qualified at top level)

    # 1. Force qualification for top-level RPCs/Actions to avoid collisions
    if stmt.keyword in ("rpc", "action"):
        return "%s:%s" % (stmt.i_module.arg, stmt.arg)

    # 2. Standard Logic
    is_top_level = stmt.parent.keyword in ("module", "submodule")
    this_mod = stmt.i_module
    parent_mod = getattr(stmt.parent, "i_module", None)

    if is_top_level or (parent_mod and this_mod.arg != parent_mod.arg):
        return "%s:%s" % (this_mod.arg, stmt.arg)

    return stmt.arg


def annotate_schema(stmt, schema):
    if schema is None:
        return None

    descriptions = []

    # 1. Base description
    desc = stmt.search_one("description")
    if desc:
        descriptions.append(desc.arg)

    # 2. Enum values description
    type_stmt = stmt.search_one("type")
    if type_stmt and type_stmt.arg == "enumeration":
        enum_descs = []
        for en in type_stmt.search("enum"):
            en_desc = en.search_one("description")
            if en_desc:
                enum_descs.append("%s: %s" % (en.arg, en_desc.arg.strip()))

        if enum_descs:
            descriptions.append(
                "Supported values:\n" + "\n".join(["  * " + d for d in enum_descs])
            )

    # 3. When statements
    when_stmts = stmt.search("when")
    for w in when_stmts:
        descriptions.append("Condition: %s" % w.arg)

    if descriptions:
        schema["description"] = "\n\n".join(descriptions)

    return schema


def produce_container(stmt, defs, opts, config_filter):
    schema = {
        "type": "object",
        "properties": produce_children(stmt, defs, opts, config_filter),
        "additionalProperties": False,
    }
    return annotate_schema(stmt, schema)


def produce_list(stmt, defs, opts, config_filter):
    schema = {
        "type": "array",
        "items": {
            "type": "object",
            "properties": produce_children(stmt, defs, opts, config_filter),
            "additionalProperties": False,
        },
    }
    return annotate_schema(stmt, schema)


def produce_leaf_list(stmt, defs, opts, config_filter):
    # Leaf lists don't recurse structure, but we pass args for consistency
    schema = {
        "type": "array",
        "items": produce_type(stmt.search_one("type"), defs, opts),
    }
    return annotate_schema(stmt, schema)


def produce_leaf(stmt, defs, opts, config_filter):
    schema = produce_type(stmt.search_one("type"), defs, opts)
    return annotate_schema(stmt, schema)


def produce_type(type_stmt, defs, opts):
    if not type_stmt:
        return {"type": "string"}

    # Handle Typedefs (Shared $defs)
    if hasattr(type_stmt, "i_typedef") and type_stmt.i_typedef:
        typedef = type_stmt.i_typedef

        if opts.schema_no_ns:
            def_key = typedef.arg
        else:
            def_key = "%s_%s" % (typedef.i_module.arg, typedef.arg)

        # Only process if not already defined (Dedup logic)
        if def_key not in defs:
            defs[def_key] = {}  # Placeholder to prevent infinite recursion
            base_type_stmt = typedef.search_one("type")
            base_schema = produce_type(base_type_stmt, defs, opts)
            defs[def_key] = annotate_schema(typedef, base_schema)

        return {"$ref": "#/$defs/%s" % def_key}

    type_name = type_stmt.arg

    # Numeric Types
    if type_name in ["int8", "int16", "int32", "uint8", "uint16", "uint32"]:
        return {"type": "integer"}
    if type_name in ["int64", "uint64"]:
        # RFC 7951: 64-bit integers are strings
        return {"type": "string", "pattern": "^-?[0-9]+$"}
    if type_name == "decimal64":
        # RFC 7951: strictly requires decimal point for canonical format in some contexts,
        # usually safer to enforce strict regex.
        return {"type": "string", "pattern": "^-?[0-9]+\\.[0-9]+$"}

    # String & Boolean
    if type_name == "string":
        schema = {"type": "string"}
        pattern = type_stmt.search_one("pattern")
        if pattern:
            schema["pattern"] = pattern.arg
        return schema
    if type_name == "boolean":
        return {"type": "boolean"}

    # Enum & Empty
    if type_name == "enumeration":
        return {"type": "string", "enum": [e.arg for e in type_stmt.search("enum")]}
    if type_name == "empty":
        return {"type": "array", "prefixItems": [{"type": "null"}], "maxItems": 1}

    # Union (Missing in your operations plugin previously)
    if type_name == "union":
        return {
            "anyOf": [produce_type(t, defs, opts) for t in type_stmt.search("type")]
        }

    # Fallback
    return {"type": "string"}


# Producer Map
producers = {
    "container": produce_container,
    "list": produce_list,
    "leaf-list": produce_leaf_list,
    "leaf": produce_leaf,
    "choice": lambda s, d, o, f: None,  # Choices are flattened in JSON
    "anydata": lambda s, d, o, f: {"type": "object"},
    "anyxml": lambda s, d, o, f: {"type": "object"},
}
