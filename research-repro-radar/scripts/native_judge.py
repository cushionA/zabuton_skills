"""Local JSON handoff for judgments performed by a host's native subagent.

This module only prepares and validates envelopes. The host must dispatch the
request to a subagent and supply its response; no agent is started here.
"""

import argparse
import hashlib
import hmac
import json
import math
import os
import re
import uuid
import warnings


VERSION = 1
PURPOSES = ("candidate", "probe")
PROBE_INSTRUCTION = "接続確認です。okをtrue、noteに ready と入れて返してください。"
PROBE_PAYLOAD = "[data]\n(なし)\n[/data]"
REQUEST_KEYS = frozenset({
    "version", "request_id", "task_sha256", "instruction", "payload", "schema", "purpose"
})
RESPONSE_KEYS = frozenset({"request_id", "task_sha256", "verdict"})
TASK_KEYS = ("instruction", "payload", "schema", "purpose")
SCHEMA_MAP_KEYS = frozenset({
    "$defs", "definitions", "properties", "patternProperties", "dependentSchemas", "dependencies",
})
SCHEMA_VALUE_KEYS = frozenset({
    "additionalItems", "additionalProperties", "contains", "contentSchema", "else", "if", "items",
    "not", "propertyNames", "then", "unevaluatedItems", "unevaluatedProperties",
})
SCHEMA_ARRAY_KEYS = frozenset({"allOf", "anyOf", "oneOf", "prefixItems"})


def _llm():
    # llm lazily imports this module when the native backend is selected.
    import llm
    return llm


def _json_value(value):
    """Reject Python-only values and JSON's nonfinite number extensions."""
    if value is None or type(value) in (str, bool, int):
        return True
    if type(value) is float:
        return math.isfinite(value)
    if type(value) is list:
        return all(_json_value(item) for item in value)
    if type(value) is dict:
        return all(type(key) is str and _json_value(item) for key, item in value.items())
    return False


def _canonical(value):
    if not _json_value(value):
        raise ValueError("finite JSON required")
    return json.dumps(value, ensure_ascii=False, allow_nan=False,
                      sort_keys=True, separators=(",", ":")).encode("utf-8")


def _local_references(schema):
    if isinstance(schema, dict):
        for key in ("$ref", "$dynamicRef", "$recursiveRef"):
            if key in schema:
                value = schema[key]
                if not isinstance(value, str) or (value and not value.startswith("#")):
                    return False
        for key in SCHEMA_MAP_KEYS:
            values = schema.get(key)
            if isinstance(values, dict) and not all(_local_references(value) for value in values.values()):
                return False
        for key in SCHEMA_VALUE_KEYS | SCHEMA_ARRAY_KEYS:
            if key in schema and not _local_references(schema[key]):
                return False
        # const/enum/default/examples and property names contain arbitrary JSON,
        # not schema keywords, and must not be interpreted as references.
    elif isinstance(schema, list):
        return all(_local_references(value) for value in schema)
    return True


def _check_schema(schema):
    llm = _llm()
    if type(schema) not in (dict, bool):
        raise llm.jsonschema.SchemaError("schema must be a JSON object or boolean")
    try:
        _canonical(schema)
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise llm.jsonschema.SchemaError("schema must contain finite JSON") from None
    try:
        # Unknown $schema dialects otherwise cause a warning with arbitrary text.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            llm.jsonschema.validators.validator_for(schema).check_schema(schema)
    except llm.jsonschema.SchemaError:
        raise
    except Exception:
        raise llm.jsonschema.SchemaError("invalid native schema") from None
    # Validation must not retrieve schemas over the network. Internal fragments
    # remain available for nested schemas and definitions.
    if not _local_references(schema):
        raise llm.jsonschema.SchemaError("native schema references must be local")


def _task_hash(request):
    return hashlib.sha256(_canonical({key: request[key] for key in TASK_KEYS})).hexdigest()


def prepare_request(instruction, payload, schema, purpose="candidate"):
    """Return a fresh request, raising SchemaError for an invalid caller schema.

    Task content is preserved exactly; the task hash excludes the fresh UUID.
    No request is persisted or dispatched by this API.
    """
    _check_schema(schema)
    if type(instruction) is not str or type(payload) is not str:
        raise ValueError("instruction and payload must be strings")
    if purpose not in PURPOSES or type(purpose) is not str:
        raise ValueError("invalid request purpose")
    if purpose == "probe" and schema != _llm().PROBE_SCHEMA:
        raise _llm().jsonschema.SchemaError("probe schema must match the built-in schema")
    request = {
        "version": VERSION, "request_id": str(uuid.uuid4()),
        "instruction": instruction, "payload": payload,
        "schema": schema, "purpose": purpose,
    }
    try:
        request["task_sha256"] = _task_hash(request)
        # Round-trip protects the envelope from later caller mutation of schema.
        return json.loads(_canonical(request))
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise ValueError("request must contain finite UTF-8 JSON") from None


def _result(stage, failure, verdict=None):
    return {"verdict": verdict, "diagnostics": {
        "stage": stage, "failure": failure, "backend": "native",
    }}


def _valid_uuid(value):
    if type(value) is not str:
        return False
    try:
        return str(uuid.UUID(value)) == value
    except ValueError:
        return False


def _offline_validate(verdict, schema):
    from referencing import Registry
    from referencing.exceptions import NoSuchResource

    def unavailable(uri):
        raise NoSuchResource(ref=uri)

    # A fragment can target arbitrary annotation data which then becomes a
    # schema. Check actual resolution as well as declared schema locations.
    validator = _llm().jsonschema.validators.validator_for(schema)
    validator(schema, registry=Registry(retrieve=unavailable)).validate(verdict)


def validate_response(request, response):
    """Return a valid verdict or a failure with only allowlisted diagnostics.

    Both arguments are decoded JSON objects. Invalid requests, identities and
    verdicts fail closed; no raw value, filename or exception is reported.
    """
    if type(request) is not dict or set(request) != REQUEST_KEYS:
        return _result("request_validation", "invalid_request")
    if (type(request["version"]) is not int or request["version"] != VERSION
            or not _valid_uuid(request["request_id"])
            or type(request["instruction"]) is not str
            or type(request["payload"]) is not str
            or type(request["purpose"]) is not str
            or request["purpose"] not in PURPOSES
            or type(request["task_sha256"]) is not str
            or re.fullmatch(r"[0-9a-f]{64}", request["task_sha256"]) is None):
        return _result("request_validation", "invalid_request")
    try:
        _canonical(request)
    except (TypeError, ValueError, UnicodeError, RecursionError):
        return _result("request_validation", "invalid_request")
    try:
        _check_schema(request["schema"])
    except Exception:
        return _result("schema_validation", "invalid_schema")
    if request["purpose"] == "probe" and request["schema"] != _llm().PROBE_SCHEMA:
        return _result("schema_validation", "invalid_probe_schema")
    if not hmac.compare_digest(request["task_sha256"], _task_hash(request)):
        return _result("request_validation", "task_hash_mismatch")
    if type(response) is not dict or set(response) != RESPONSE_KEYS:
        return _result("response_validation", "invalid_response")
    try:
        _canonical(response)
    except (TypeError, ValueError, UnicodeError, RecursionError):
        return _result("response_validation", "invalid_response")
    if (not _valid_uuid(response["request_id"])
            or type(response["task_sha256"]) is not str
            or re.fullmatch(r"[0-9a-f]{64}", response["task_sha256"]) is None
            or not hmac.compare_digest(response["request_id"], request["request_id"])
            or not hmac.compare_digest(response["task_sha256"], request["task_sha256"])):
        return _result("identity_validation", "identity_mismatch")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            _offline_validate(response["verdict"], request["schema"])
            valid = _llm()._valid(response["verdict"], request["schema"])
    except Exception:
        valid = False
    if not valid:
        return _result("schema_validation", "schema_violation")
    if (request["purpose"] == "probe"
            and response["verdict"] != {"ok": True, "note": "ready"}):
        return _result("probe_validation", "probe_mismatch")
    return _result("complete", None, response["verdict"])


def _reject_constant(_value):
    raise ValueError("nonfinite JSON")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def load_json_file(path):
    """Read strict local JSON; callers must mask failures before reporting."""
    with open(path, encoding="utf-8") as handle:
        value = json.load(handle, parse_constant=_reject_constant,
                          object_pairs_hook=_unique_object)
    _canonical(value)
    return value


def _read_text_file(path):
    with open(path, encoding="utf-8", newline="") as handle:
        return handle.read()


def _write_request(path, request):
    # Serialize before opening; malformed data cannot leave a partial request.
    content = _canonical(request) + b"\n"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = None
            handle.write(content)
    except Exception:
        if descriptor is not None:
            os.close(descriptor)
        try:
            os.unlink(path)
        except OSError:
            pass
        raise


class _MaskedArgumentError(ValueError):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, _message):
        raise _MaskedArgumentError("invalid CLI arguments")


def _parser():
    parser = _Parser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True,
                                      parser_class=_Parser)
    prepare = subcommands.add_parser("prepare", help="write a native subagent request")
    prepare.add_argument("--instruction-file")
    prepare.add_argument("--payload-file")
    prepare.add_argument("--schema-file")
    prepare.add_argument("--request-file", required=True)
    prepare.add_argument("--purpose", choices=PURPOSES, default="candidate")
    validate = subcommands.add_parser("validate", help="validate a native subagent response")
    validate.add_argument("--request-file", required=True)
    validate.add_argument("--response-file", required=True)
    return parser


def main(argv=None):
    try:
        args = _parser().parse_args(argv)
    except _MaskedArgumentError:
        print(json.dumps(_result("arguments", "invalid_arguments")))
        return 1
    try:
        llm = _llm()
    except Exception:
        print(json.dumps(_result("initialization", "backend_unavailable")))
        return 1
    if args.command == "prepare":
        input_paths = (args.instruction_file, args.payload_file, args.schema_file)
        if args.purpose == "probe" and not any(input_paths):
            instruction, payload, schema = PROBE_INSTRUCTION, PROBE_PAYLOAD, llm.PROBE_SCHEMA
        elif all(input_paths):
            try:
                instruction = _read_text_file(args.instruction_file)
                payload = _read_text_file(args.payload_file)
                schema = load_json_file(args.schema_file)
            except Exception:
                print(json.dumps(_result("request_input", "input_unavailable")))
                return 1
        else:
            print(json.dumps(_result("arguments", "invalid_arguments")))
            return 1
        try:
            request = prepare_request(instruction, payload, schema, args.purpose)
        except llm.jsonschema.SchemaError:
            print(json.dumps(_result("schema_validation", "invalid_schema")))
            return 1
        except Exception:
            print(json.dumps(_result("request_validation", "invalid_request")))
            return 1
        try:
            _write_request(args.request_file, request)
        except FileExistsError:
            print(json.dumps(_result("request_output", "request_exists")))
            return 1
        except Exception:
            print(json.dumps(_result("request_output", "output_unavailable")))
            return 1
        # The request contains arbitrary task data and is never printed to logs.
        print(json.dumps({"request_id": request["request_id"],
                          "task_sha256": request["task_sha256"],
                          "diagnostics": _result("request_prepared", None)["diagnostics"]}))
        return 0
    try:
        request = load_json_file(args.request_file)
    except Exception:
        print(json.dumps(_result("request_input", "input_unavailable")))
        return 1
    try:
        response = load_json_file(args.response_file)
    except Exception:
        print(json.dumps(_result("response_input", "input_unavailable")))
        return 1
    result = validate_response(request, response)
    print(json.dumps(result, allow_nan=False))
    return 0 if result["verdict"] is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
