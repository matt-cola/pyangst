"""JSON Schema output plugin - RFC 7951 Compliant (Draft 2020-12) with $ref support"""

from __future__ import print_function
import optparse
import logging
import json
from pyang import plugin


def pyang_plugin_init():
    plugin.register_plugin(JSONSchemaPlugin())


class JSONSchemaPlugin(plugin.PyangPlugin):
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
                help="Only include nodes where config is true (exclude read-only/state data)",
            ),
        ]
        group = optparser.add_option_group("JSON Schema-specific options")
        group.add_options(optlist)

    def setup_fmt(self, ctx):
        ctx.implicit_errors = False

    def emit(self, ctx, modules, fd):
        if ctx.opts.schema_debug:
            logging.basicConfig(level=logging.DEBUG)

        root_module = modules[0]
        self.definitions = {}

        result = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": root_module.arg,
            "type": "object",
            "properties": produce_children(root_module, self.definitions, ctx.opts),
            "$defs": self.definitions,
        }

        if not result["$defs"]:
            del result["$defs"]

        fd.write(json.dumps(result, indent=2))


def produce_children(stmt, defs, opts):
    props = {}
    if hasattr(stmt, "i_children"):
        for child in stmt.i_children:
            # Check if the node is operational/state data when config-only is requested.
            # 'i_config' is computed by pyang and handles inheritance from parent nodes.
            if getattr(opts, "schema_config_only", False):
                if hasattr(child, "i_config") and child.i_config is False:
                    continue

            if child.keyword in producers:
                member_name = qualify_name(child)
                schema = producers[child.keyword](child, defs, opts)
                if schema:
                    props[member_name] = schema
    return props


def qualify_name(stmt):
    is_top_level = stmt.parent.keyword == "module"
    this_mod = stmt.i_module
    parent_mod = getattr(stmt.parent, "i_module", None)
    if is_top_level or (parent_mod and this_mod.arg != parent_mod.arg):
        return "%s:%s" % (this_mod.arg, stmt.arg)
    return stmt.arg


def annotate_schema(stmt, schema):
    if schema is None:
        return None
    desc = stmt.search_one("description")
    if desc:
        schema["description"] = desc.arg
    default = stmt.search_one("default")
    if default:
        schema["default"] = default.arg
    return schema


def produce_container(stmt, defs, opts):
    schema = {
        "type": "object",
        "properties": produce_children(stmt, defs, opts),
        "additionalProperties": False,
    }
    return annotate_schema(stmt, schema)


def produce_list(stmt, defs, opts):
    schema = {
        "type": "array",
        "items": {
            "type": "object",
            "properties": produce_children(stmt, defs, opts),
            "additionalProperties": False,
        },
    }
    return annotate_schema(stmt, schema)


def produce_leaf_list(stmt, defs, opts):
    schema = {
        "type": "array",
        "items": produce_type(stmt.search_one("type"), defs, opts),
    }
    return annotate_schema(stmt, schema)


def produce_leaf(stmt, defs, opts):
    schema = produce_type(stmt.search_one("type"), defs, opts)
    return annotate_schema(stmt, schema)


def produce_type(type_stmt, defs, opts):
    if not type_stmt:
        return {"type": "string"}

    if hasattr(type_stmt, "i_typedef") and type_stmt.i_typedef:
        typedef = type_stmt.i_typedef

        if opts.schema_no_ns:
            def_key = typedef.arg
        else:
            def_key = "%s_%s" % (typedef.i_module.arg, typedef.arg)

        if def_key not in defs:
            defs[def_key] = {}
            base_type_stmt = typedef.search_one("type")
            base_schema = produce_type(base_type_stmt, defs, opts)
            defs[def_key] = annotate_schema(typedef, base_schema)

        return {"$ref": "#/$defs/%s" % def_key}

    type_name = type_stmt.arg
    if type_name in ["int8", "int16", "int32", "uint8", "uint16", "uint32"]:
        return {"type": "integer"}
    if type_name in ["int64", "uint64"]:
        return {"type": "string", "pattern": "^-?[0-9]+$"}
    if type_name == "decimal64":
        return {"type": "string", "pattern": "^-?[0-9]+\\.[0-9]+$"}
    if type_name == "string":
        schema = {"type": "string"}
        pattern = type_stmt.search_one("pattern")
        if pattern:
            schema["pattern"] = pattern.arg
        return schema
    if type_name == "boolean":
        return {"type": "boolean"}
    if type_name == "enumeration":
        return {"type": "string", "enum": [e.arg for e in type_stmt.search("enum")]}
    if type_name == "empty":
        return {"type": "array", "prefixItems": [{"type": "null"}], "maxItems": 1}
    if type_name == "union":
        return {
            "anyOf": [produce_type(t, defs, opts) for t in type_stmt.search("type")]
        }

    return {"type": "string"}


producers = {
    "container": produce_container,
    "list": produce_list,
    "leaf-list": produce_leaf_list,
    "leaf": produce_leaf,
    "choice": lambda s, d, o: None,
    "anydata": lambda s, d, o: {"type": "object"},
    "anyxml": lambda s, d, o: {"type": "object"},
}
