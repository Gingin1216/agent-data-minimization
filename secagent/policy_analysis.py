#!/usr/bin/env python3
import sys

from jsonschema import Draft202012Validator
from z3 import *

from .role_analyzer import regex_to_z3_expr_search

# Define a global Z3 datatype for null.
NullType = Datatype('NullType')
NullType.declare('null')
NullType = NullType.create()

# Define a Z3 datatype for arrays.
# ArrayWrapper has two fields:
#   a: an array from Int to Type
#   len: an integer representing the length of the array.

# Array wrapper for arrays of strings.
ArrayWrapper_string = Datatype('ArrayWrapper_string')
ArrayWrapper_string.declare('mkArrayWrapper', ('a', ArraySort(IntSort(), StringSort())), ('len', IntSort()))
ArrayWrapper_string = ArrayWrapper_string.create()

# Array wrapper for arrays of integers.
ArrayWrapper_integer = Datatype('ArrayWrapper_integer')
ArrayWrapper_integer.declare('mkArrayWrapper', ('a', ArraySort(IntSort(), IntSort())), ('len', IntSort()))
ArrayWrapper_integer = ArrayWrapper_integer.create()

# Array wrapper for arrays of reals (numbers).
ArrayWrapper_number = Datatype('ArrayWrapper_number')
ArrayWrapper_number.declare('mkArrayWrapper', ('a', ArraySort(IntSort(), RealSort())), ('len', IntSort()))
ArrayWrapper_number = ArrayWrapper_number.create()

# Array wrapper for arrays of booleans.
ArrayWrapper_boolean = Datatype('ArrayWrapper_boolean')
ArrayWrapper_boolean.declare('mkArrayWrapper', ('a', ArraySort(IntSort(), BoolSort())), ('len', IntSort()))
ArrayWrapper_boolean = ArrayWrapper_boolean.create()

# Array wrapper for arrays of null.
ArrayWrapper_null = Datatype('ArrayWrapper_null')
ArrayWrapper_null.declare('mkArrayWrapper', ('a', ArraySort(IntSort(), NullType)), ('len', IntSort()))
ArrayWrapper_null = ArrayWrapper_null.create()

# A list of all array wrappers.
ARRAY_WRAPPERS = [ArrayWrapper_string, ArrayWrapper_integer, ArrayWrapper_number, ArrayWrapper_boolean, ArrayWrapper_null]

# Map each array wrapper sort to its accessors (the 'a' field and the 'len' field).
ARRAY_WRAPPER_ACCESSORS = {
    ArrayWrapper_string: (ArrayWrapper_string.a, ArrayWrapper_string.len),
    ArrayWrapper_integer: (ArrayWrapper_integer.a, ArrayWrapper_integer.len),
    ArrayWrapper_number: (ArrayWrapper_number.a, ArrayWrapper_number.len),
    ArrayWrapper_boolean: (ArrayWrapper_boolean.a, ArrayWrapper_boolean.len),
    ArrayWrapper_null: (ArrayWrapper_null.a, ArrayWrapper_null.len)
}

_SCHEMA_SUBSCHEMAS = {"not", "if", "then", "else", "items"}
_SCHEMA_GROUPS = {"anyOf", "allOf", "oneOf"}
_SCHEMA_KEYWORDS = _SCHEMA_SUBSCHEMAS | _SCHEMA_GROUPS | {
    "type", "const", "enum",
    "pattern", "minLength", "maxLength",
    "multipleOf", "minimum", "exclusiveMinimum", "maximum", "exclusiveMaximum",
    "minItems", "maxItems",
    # The runtime validator accepts these as annotations, without asserting them.
    "$schema", "$comment", "title", "description", "default", "examples",
    "deprecated", "readOnly", "writeOnly",
}
_SCHEMA_DIALECT = Draft202012Validator.META_SCHEMA["$id"]


def _require_supported_schema(schema):
    """Check all schema positions before solving any type-specific branch."""
    if isinstance(schema, bool):
        return
    if not isinstance(schema, dict):
        raise NotImplementedError("Expected a boolean or object schema")
    if "$schema" in schema and schema["$schema"] not in (_SCHEMA_DIALECT, _SCHEMA_DIALECT + "#"):
        raise NotImplementedError("Only the JSON Schema 2020-12 dialect is modelled")
    unsupported = schema.keys() - _SCHEMA_KEYWORDS
    if unsupported:
        raise NotImplementedError(f"Unsupported schema keywords: {', '.join(sorted(unsupported))}")

    for keyword in _SCHEMA_SUBSCHEMAS & schema.keys():
        _require_supported_schema(schema[keyword])
    for keyword in _SCHEMA_GROUPS & schema.keys():
        if not isinstance(schema[keyword], list):
            raise NotImplementedError(f"Expected a list of schemas for {keyword}")
        for subschema in schema[keyword]:
            _require_supported_schema(subschema)


def _literal_constraint(var, value):
    """Match a JSON value without coercing it to a different Z3 sort."""
    sort = var.sort()
    if value is None:
        return var == NullType.null if sort == NullType else BoolVal(False)
    if isinstance(value, bool):
        return var == BoolVal(value) if sort == BoolSort() else BoolVal(False)
    if isinstance(value, str):
        if sort != StringSort():
            return BoolVal(False)
        if any(ord(character) > 0x2FFFF for character in value):
            raise NotImplementedError("String literal exceeds the Z3 Unicode alphabet")
        # Z3 interprets Unicode escapes in StringVal. Insert literal backslashes
        # as characters so that a JSON string containing an escape stays literal.
        parts = value.split("\\")
        literal = StringVal(parts[0])
        for part in parts[1:]:
            literal = Concat(literal, Unit(CharVal("\\")), StringVal(part))
        return var == literal
    if isinstance(value, (int, float)):
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        if sort == IntSort() and isinstance(value, int):
            return var == IntVal(value)
        if sort == RealSort():
            return var == RealVal(value)
        return BoolVal(False)
    if isinstance(value, list):
        item_types = {float if type(item) is int else type(item) for item in value}
        if len(item_types) > 1 or item_types - {str, float, bool, type(None)}:
            raise NotImplementedError("Cannot model this array literal in Z3")
        if sort not in ARRAY_WRAPPER_ACCESSORS:
            return BoolVal(False)
        array, length = ARRAY_WRAPPER_ACCESSORS[sort]
        items = [_literal_constraint(Select(array(var), i), item) for i, item in enumerate(value)]
        # The cells beyond the declared length are not part of the JSON value.
        return And(length(var) == len(value), *items)
    raise NotImplementedError(f"Cannot model a {type(value).__name__} literal in Z3")


def build_constraints(var, schema):
    """Build Z3 constraints for a schema in the supported keyword subset."""
    _require_supported_schema(schema)
    return _build_constraints(var, schema)


def _build_constraints(var, schema):
    if isinstance(schema, bool):
        return BoolVal(schema)
    constraint = True

    if "const" in schema:
        constraint = And(constraint, _literal_constraint(var, schema["const"]))

    if "enum" in schema:
        constraint = And(constraint, Or([_literal_constraint(var, item) for item in schema["enum"]]))

    # anyOf: disjunction of subschemas.
    if "anyOf" in schema:
        branch_constraints = [_build_constraints(var, subschema) for subschema in schema["anyOf"]]
        constraint = And(constraint, Or(branch_constraints))

    # allOf: all subschemas must hold.
    if "allOf" in schema:
        all_constraints = [_build_constraints(var, subschema) for subschema in schema["allOf"]]
        constraint = And(constraint, And(all_constraints))

    # oneOf: exactly one subschema must hold.
    if "oneOf" in schema:
        oneof_constraints = [_build_constraints(var, subschema) for subschema in schema["oneOf"]]
        constraint = And(constraint, Sum([If(c, 1, 0) for c in oneof_constraints]) == 1)

    # not: the subschema must not hold.
    if "not" in schema:
        constraint = And(constraint, Not(_build_constraints(var, schema["not"])))

    # if/then/else: conditional constraints.
    if "if" in schema:
        if_constraint = _build_constraints(var, schema["if"])
        if "then" in schema:
            then_constraint = _build_constraints(var, schema["then"])
            constraint = And(constraint, Implies(if_constraint, then_constraint))
        if "else" in schema:
            else_constraint = _build_constraints(var, schema["else"])
            constraint = And(constraint, Implies(Not(if_constraint), else_constraint))

    # Type check.
    if "type" in schema:
        t = schema["type"]
        t_list = []
        if isinstance(t, list):
            t_list = t
        else:
            t_list.append(t)
        type_constraint = []
        for t in t_list:
            if t == "string":
                type_constraint.append(var.sort() == StringSort())
            elif t == "array":
                type_constraint.append(Or([var.sort() == aw for aw in ARRAY_WRAPPERS]))
            elif t == "integer":
                type_constraint.append(IsInt(var) if var.sort() == RealSort() else var.sort() == IntSort())
            elif t == "number":
                type_constraint.append(var.sort() in (IntSort(), RealSort()))
            elif t == "boolean":
                type_constraint.append(var.sort() == BoolSort())
            elif t == "null":
                type_constraint.append(var.sort() == NullType)
            else:
                raise NotImplementedError(f"Cannot model schema type {t!r} in Z3")
        constraint = And(constraint, Or(type_constraint))

    # Type-specific constraints.
    if var.sort() == StringSort():
        if "pattern" in schema:
            pat = schema["pattern"]
            # jsonschema enforces "pattern" with re.search, so the language
            # modelled here is the searched one, not a whole-string match.
            regex = regex_to_z3_expr_search(pat)
            constraint = And(constraint, InRe(var, regex))
        if "minLength" in schema:
            constraint = And(constraint, Length(var) >= schema["minLength"])
        if "maxLength" in schema:
            constraint = And(constraint, Length(var) <= schema["maxLength"])
    elif var.sort() in ARRAY_WRAPPERS:
        a_accessor, len_accessor = ARRAY_WRAPPER_ACCESSORS[var.sort()]
        constraint = And(constraint, len_accessor(var) >= 0)
        if "minItems" in schema:
            constraint = And(constraint, len_accessor(var) >= schema["minItems"])
        if "maxItems" in schema:
            constraint = And(constraint, len_accessor(var) <= schema["maxItems"])
        if "items" in schema:
            items_schema = schema["items"]
            i = Int('i')
            constraint = And(constraint, ForAll(i, Implies(And(i >= 0, i < len_accessor(var)),
                                                           _build_constraints(Select(a_accessor(var), i), items_schema))))
    elif var.sort() in (IntSort(), RealSort()):
        if "multipleOf" in schema:
            m = schema["multipleOf"]
            if var.sort() == IntSort() and isinstance(m, int):
                constraint = And(constraint, var % m == 0)
            else:
                k = FreshInt('k_mult')
                constraint = And(constraint, Exists(k, var == RealVal(m) * k))
        if "exclusiveMinimum" in schema:
            constraint = And(constraint, var > schema["exclusiveMinimum"])
        if "minimum" in schema:
            constraint = And(constraint, var >= schema["minimum"])
        if "exclusiveMaximum" in schema:
            constraint = And(constraint, var < schema["exclusiveMaximum"])
        if "maximum" in schema:
            constraint = And(constraint, var <= schema["maximum"])
    elif var.sort() == BoolSort():
        pass
    elif var.sort() == NullType:
        constraint = And(constraint, var == NullType.null)

    return constraint


SOLVER_SAT = "sat"
SOLVER_UNSAT = "unsat"
SOLVER_UNDECIDED = "undecided"


def solve_schema(schema):
    """
    Given a JSON Schema (as a dict), determine if there is a value that satisfies it.
    Uses the schema's declared top-level type when available, or tries all supported sorts.
    Returns (SOLVER_SAT, model, var) when a satisfying value was found,
    (SOLVER_UNSAT, None, var) when there is provably none, and
    (SOLVER_UNDECIDED, None, var) when no candidate type could be built, the
    solver timed out, or the schema used a construct that cannot be translated.
    """
    try:
        _require_supported_schema(schema)
    except NotImplementedError as error:
        print(f"solve_schema: {error}", file=sys.stderr)
        return SOLVER_UNDECIDED, None, None

    var_list = []
    # Determine the top-level type.
    if isinstance(schema, dict) and "type" in schema:
        t = schema["type"]
        t_list = []
        if isinstance(t, list):
            t_list = t
        else:
            t_list.append(t)
        for t in t_list:
            if t == "string":
                var_list.append(String('x'))
            elif t == "array":
                # Choose an array wrapper based on the "items" type if provided.
                items = schema.get("items")
                if isinstance(items, dict) and "type" in items:
                    item_type = items["type"]
                    if item_type == "string":
                        var_list.append(Const('x', ArrayWrapper_string))
                    elif item_type == "integer":
                        var_list.append(Const('x', ArrayWrapper_integer))
                    elif item_type == "number":
                        var_list.append(Const('x', ArrayWrapper_number))
                    elif item_type == "boolean":
                        var_list.append(Const('x', ArrayWrapper_boolean))
                    elif item_type == "null":
                        var_list.append(Const('x', ArrayWrapper_null))
                    else:
                        for aw in ARRAY_WRAPPERS:
                            var_list.append(Const('x', aw))
                else:
                    for aw in ARRAY_WRAPPERS:
                        var_list.append(Const('x', aw))
            elif t == "integer":
                var_list.append(Int('x'))
            elif t == "number":
                var_list.append(Real('x'))
            elif t == "boolean":
                var_list.append(Bool('x'))
            elif t == "null":
                var_list.append(Const('x', NullType))
    else:
        var_list = [String('x'), Int('x'), Real('x'), Bool('x'), Const('x', NullType)]
        var_list.extend(Const('x', aw) for aw in ARRAY_WRAPPERS)

    undecided = False
    var = None
    for var in var_list:
        try:
            cons = _build_constraints(var, schema)
            s = Solver()
            s.add(cons)
            s.set("timeout", 5000)
            result = s.check()
            if result == sat:
                model = s.model()
                return SOLVER_SAT, model, var
            if result == unknown:
                undecided = True
                print(f"solve_schema: solver gave up on {var.sort()}: {s.reason_unknown()}", file=sys.stderr)
        except Exception as e:
            # An untranslatable regex or an unsupported keyword leaves this
            # branch unproved; it does not make the branch unsatisfiable.
            undecided = True
            print(f"solve_schema: cannot decide {var.sort()}: {e}", file=sys.stderr)
    if var is None:
        # No candidate type matched the schema, so nothing was ever checked.
        undecided = True
    return (SOLVER_UNDECIDED if undecided else SOLVER_UNSAT), None, var


def security_policy_analysis(security_policy):
    warnings = []
    if security_policy is None:
        return warnings
    for tool_name, policies in security_policy.items():
        n = len(policies)
        for i in range(n):
            for j in range(i+1, n):
                for arg_name, restriction_1 in policies[i][2].items():
                    if arg_name in policies[j][2]:
                        restriction_2 = policies[j][2][arg_name]
                        # print(restriction_1, restriction_2)
                        if isinstance(restriction_1, dict) and isinstance(restriction_2, dict):  # json schema
                            schema = {
                                "allOf": [
                                    restriction_1,
                                    restriction_2
                                ]
                            }
                            status, model, var = solve_schema(schema)
                            if status == SOLVER_SAT:
                                warnings.append(f"Policy Warning: {restriction_1} and {restriction_2} overlap. Possible value: {model[var]}.")
                            elif status == SOLVER_UNDECIDED:
                                warnings.append(f"Policy Warning: cannot determine whether {restriction_1} and {restriction_2} overlap.")
    return warnings


def security_policy_subset_check(ori_policy, new_policy):
    ori_policy_filtered = {tool: [p for p in policies if p[0] == 100] for tool, policies in ori_policy.items()}
    new_policy_filtered = {tool: [p for p in policies if p[0] == 100] for tool, policies in new_policy.items()}
    # if tool: [], [] is empty list, delete it.
    ori_policy_filtered = {tool: policies for tool, policies in ori_policy_filtered.items() if len(policies) > 0}
    new_policy_filtered = {tool: policies for tool, policies in new_policy_filtered.items() if len(policies) > 0}
    print(ori_policy_filtered, new_policy_filtered, file=sys.stderr)
    for tool, policies in list(ori_policy_filtered.items()) + list(new_policy_filtered.items()):
        if len(policies) > 1:
            print(f"Tool '{tool}' has {len(policies)} generated policy entries, expected one.", file=sys.stderr)
            return False
    for tool, new_policies in new_policy_filtered.items():
        if tool not in ori_policy_filtered:
            return False
    for tool, new_policies in new_policy_filtered.items():
        ori_policies = ori_policy_filtered[tool]
        print(ori_policies, new_policies, file=sys.stderr)
        for arg_name, restriction_1 in ori_policies[0][2].items():
            if arg_name not in new_policies[0][2]:
                return False
            restriction_2 = new_policies[0][2][arg_name]
            print(restriction_1, restriction_2, file=sys.stderr)
            if isinstance(restriction_1, dict) and isinstance(restriction_2, dict):  # json schema
                schema = {
                    "allOf": [
                        {"not": restriction_1},
                        restriction_2
                    ]
                }
                # The old policy cannot restrict where we look for a value
                # allowed only by the candidate policy.
                if "type" in restriction_2:
                    schema["type"] = restriction_2["type"]
                status, model, var = solve_schema(schema)
                if status == SOLVER_SAT:
                    print(f"Found counter example: {model[var]}", file=sys.stderr)
                    return False
                if status != SOLVER_UNSAT:
                    # Containment was not proved, so it cannot be assumed.
                    print(f"Cannot prove {restriction_2} is a subset of {restriction_1}.", file=sys.stderr)
                    return False
            else:
                if restriction_1 != restriction_2:
                    return False
    return True


if __name__ == "__main__":
    warnings = security_policy_analysis(
        security_policy={
            "send_money": [
                (1, 0, {"recipient": {"type": "string", "pattern": "UK12345678901234567890"}}, 0),
                (2, 1, {"recipient": {"type": "string", "pattern": "SE3550000000054910000003"}}, 0),
            ],
            "send_email": [
                (1, 0, {"recipient": {"type": "string", "pattern": "[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"}}, 0),
                (2, 1, {"recipient": {"type": "string", "pattern": "agent@example.com"}}, 0),
            ]
        }
    )
    print(warnings)
