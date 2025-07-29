import fitz  # PyMuPDF
import yaml
import json
import re
import unicodedata
from collections import defaultdict

pdf_path = "codecommit-api.pdf"  # 👈 Update this path to match your file location

def parse_req_param(span):
    uni_text = clean_text(span["text"])
    if span["size"] == 12 and "Bold" in span["font"] and re.match(r'^[a-zA-Z]+(?:[A-Z][a-z0-9]*)*$', uni_text):
        return {"name": uni_text}
    if span["size"] == 12 and "Regular" in span["font"] and uni_text.startswith("Type:"):
        return {"type" : uni_text.split(':')[-1].strip()}
    if span["size"] == 12 and "Regular" in span["font"] and uni_text.startswith("Required:"):
        return {"required" : uni_text.split(':')[-1].strip()}

def is_api_title(span):
    match =  re.match(r'^[A-Z][A-Za-z]+(?:[A-Z][A-Za-z]+)+$', clean_text(span["text"]))
    return span["size"] == 18 and "Bold" in span["font"] and match

def is_section_heading(text):
    return text.strip() in [
        "Request Syntax", "Request Parameters",
        "Response Syntax", "Response Elements", "Errors", "Examples", "See Also"
    ]

def is_code_block(span):
    return "Courier" in span["font"] or "Mono" in span["font"]

import unicodedata

def clean_text(text):
    text = unicodedata.normalize("NFKD", text)
    replacements = {
        "\u201c": '"',  # “
        "\u201d": '"',  # ”
        "“": '"',
        "”": '"',
        "\u2018": "'",  # ‘
        "\u2019": "'",  # ’
        "‘": "'",
        "’": "'",
        "\u2013": "-",  # –
        "\u2014": "-",  # —
    }

    for orig, repl in replacements.items():
        text = text.replace(orig, repl)

    return text.strip()


def safe_parse_json(text):
    try:
        return json.loads(text)
    except Exception:
        return {}

def get_action_from_operation_id(operation_id: str) -> str:
    prefix_to_action = {
        "Get": "get",
        "Delete": "delete"
    }

    # Iterate through the prefixes in the dictionary
    for prefix, action in prefix_to_action.items():
        if operation_id.startswith(prefix):
            return action

    # If no prefix matches, default to "post"
    return "post"


def extract_parameter_schemaDict(params_text):
    schema = {"type": "object", "properties": {}, "required": []}

    for param in params_text:
        param_name = param.get("name")
        param_type = param.get("type")
        is_required = param.get("required", "No")

        if not param_name or not param_type:
            continue

        # Initialize property
        param_type = param_type.strip().lower()  # Normalize input

        if param_type in ["array of strings", "array of"]:
            prop = {
                "type": "array",
                "items": {"type": "string"}
            }

        elif param_type == "string to string map":
            prop = {
                "type": "object",
                "additionalProperties": {"type": "string"}
            }

        elif param_type == "base64-encoded binary data object":
            prop = {
                "type": "string",
                "format": "byte"
            }

        elif param_type == "":
            prop = {
                "type": "string"
            }

        else:
            prop = {
                "type": param_type
            }


        # Optional constraints
        if "minLength" in param:
            prop["minLength"] = param["minLength"]
        if "maxLength" in param:
            prop["maxLength"] = param["maxLength"]
        if "pattern" in param:
            prop["pattern"] = param["pattern"]

        schema["properties"][param_name] = prop

        if is_required == "Yes":
            schema["required"].append(param_name)

    return schema



def extract_parameter_schema(params_text):
    lines = params_text.splitlines()
    schema = {"type": "object", "properties": {}, "required": []}
    param_name = None

    for line in lines:
        line = line.strip()
        if not line:
            continue

        if re.match(r'^[a-zA-Z0-9]+$', line):
            param_name = line
            schema["properties"][param_name] = {"type": "string"}  # default
        elif "Type:" in line:
            if param_name:
                if "String" in line:
                    schema["properties"][param_name]["type"] = "string"
                elif "Boolean" in line:
                    schema["properties"][param_name]["type"] = "boolean"
                elif "Integer" in line:
                    schema["properties"][param_name]["type"] = "integer"
        elif "Required: Yes" in line and param_name:
            schema["required"].append(param_name)
        elif "Length Constraints:" in line and param_name:
            m = re.findall(r'(\d+)', line)
            if len(m) == 2:
                schema["properties"][param_name]["minLength"] = int(m[0])
                schema["properties"][param_name]["maxLength"] = int(m[1])
        elif "Pattern:" in line and param_name:
            pattern = line.split("Pattern:")[-1].strip()
            schema["properties"][param_name]["pattern"] = pattern

    return schema

# --- Parse the PDF ---
apis = {}
current_api = None
state = None

doc = fitz.open(pdf_path)

for page in doc:
    if page.number >= 33 and page.number <= 550: #33-550
        blocks = page.get_text("dict")["blocks"]
        for block in blocks:
            req_param_obj = [{"type": "object", "name": "object", "required": "boolean"}]
            if block.get("number") == 0:
                continue
            for line in block.get("lines", []):
                for span in line["spans"]:
                    text = clean_text(span["text"])
                    if not text:
                        continue

                    if is_api_title(span):
                        current_api = text
                        apis[current_api] = {
                            "summary": "",
                            "request_body": "",
                            "request_parameters": [],
                            "response_body": "",
                            "response_elements": ""
                        }
                        state = "InOperation"

                    elif is_section_heading(text):
                        state = {
                            "Request Syntax": "InRequestSyntax",
                            "Request Parameters": "InRequestParameters",
                            "Response Syntax": "InResponseSyntax",
                            "Response Elements": "InResponseElements",
                            "Errors": "Ignore",
                            "Examples": "Ignore", 
                            "See Also": "Ignore"
                        }[text]

                    elif state and current_api and state != "Ignore":
                        key = {
                            "InRequestSyntax": "request_body",
                            "InRequestParameters": "request_parameters",
                            "InResponseSyntax": "response_body",
                            "InResponseElements": "response_elements",
                            "InOperation": "summary"
                        }.get(state, "")

                        if key:
                            if key != "request_parameters":
                                apis[current_api][key] += text + ("\n" if is_code_block(span) else " ")
                            else:
                                new_data = parse_req_param(span)
                                if new_data:
                                    found = False

                                    # Try to update an existing dict that doesn't have the new_data key
                                    for d in apis[current_api][key]:
                                        # Check if there's any intersection key that is missing in existing dict
                                        if any(k not in d for k in new_data.keys()):
                                            d.update(new_data)
                                            found = True
                                            break

                                    # If no suitable dict found, append a new one
                                    if not found:
                                        apis[current_api][key].append(new_data)
                                else:
                                    print(f"-->> skip.. {span['text']}")
    else:
        print(f"Skip page no - {page.number}")

# --- Convert to OpenAPI format ---
openapi_spec = {
    "openapi": "3.0.0",
    "info": {
        "title": "AWS CodeCommit API",
        "version": "2015-04-13"
    },
    "paths": {}
}

for api, content in apis.items():
    path = f"/{api}"
    request_schema = extract_parameter_schemaDict(content["request_parameters"])
    request_example = safe_parse_json(content["request_body"])
    response_example = safe_parse_json(content["response_body"])

    openapi_spec["paths"][path] = {
        get_action_from_operation_id(api): {
            "summary": content["summary"].strip(),
            "operationId": api,
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": request_schema,
                        "example": request_example
                    }
                }
            },
            "responses": {
                "200": {
                    "description": "Success",
                    "content": {
                        "application/json": {
                            "example": response_example
                        }
                    }
                },
                "400": {"description": "Client Error"},
                "500": {"description": "Server Error"}
            }
        }
    }

# --- Export as YAML and JSON ---
with open("codecommit_openapi_full.yaml", "w") as f_yaml:
    yaml.dump(openapi_spec, f_yaml, sort_keys=False)

with open("codecommit_openapi_full.json", "w") as f_json:
    json.dump(openapi_spec, f_json, indent=2)

print("✅ Done! Files generated:")
print(" - codecommit_openapi_full.yaml")
print(" - codecommit_openapi_full.json")
