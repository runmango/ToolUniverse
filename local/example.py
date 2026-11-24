"""A real, key-free UniProt query through the local ToolUniverse SDK."""
import json
from run import configure

configure()
from tooluniverse import ToolUniverse

tu = ToolUniverse()
try:
    tu.load_tools(include_tools=["UniProt_get_entry_by_accession"])
    result = tu.run({"name": "UniProt_get_entry_by_accession", "arguments": {"accession": "P05067"}})
    print(json.dumps(result, ensure_ascii=False, indent=2))
finally:
    tu.close()
