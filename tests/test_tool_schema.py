"""Validate real docstring parsing, not the no-op decorator used in API tests.

Test-only dependency: docstring-parser (already used by AstrBot).
"""
import ast
from pathlib import Path
import unittest
import docstring_parser


class ToolSchemas(unittest.TestCase):
    def test_all_tool_schemas(self):
        root=Path(__file__).resolve().parents[1]
        names=set()
        for file in (root/'main.py',root/'extras.py'):
            for node in ast.walk(ast.parse(file.read_text(encoding='utf-8'))):
                if not isinstance(node,ast.AsyncFunctionDef): continue
                if not any(isinstance(d,ast.Call) and isinstance(d.func,ast.Attribute)
                           and d.func.attr=='llm_tool' for d in node.decorator_list): continue
                for decorator in node.decorator_list:
                    if isinstance(decorator,ast.Call) and isinstance(decorator.func,ast.Attribute) and decorator.func.attr=='llm_tool':
                        names.update(keyword.value.value for keyword in decorator.keywords if keyword.arg=='name')
                parsed=docstring_parser.parse(ast.get_docstring(node))
                expected={a.arg for a in node.args.args}-{'self','event'}
                self.assertEqual({p.arg_name for p in parsed.params},expected,node.name)
                for param in parsed.params:
                    self.assertIn(param.type_name,{'string','number','object','array','boolean'},node.name)
        self.assertEqual(names, {'qq_profile_like', 'server_status_image', 'pica_commands', 'pixiv_commands', 'jm_commands'})

    def test_old_type_is_rejected(self):
        parsed=docstring_parser.parse('Test.\n\nArgs:\n    page(integer): page number')
        self.assertEqual(parsed.params[0].type_name,'integer')
        self.assertNotIn(parsed.params[0].type_name,{'string','number','object','array','boolean'})

if __name__=='__main__': unittest.main()
