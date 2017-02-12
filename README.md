# cs335_compiler

We are building a compiler for the course project of CS335A. Our source language is C and destination language is x86, whereas our implementation is in Python.

For the first milestone of the project, we have used Python Lex and Yacc (PLY) for making a scanner and a parser that can parse C code using rules from the C99 grammar. Our implementation builds upon that by Eli Bendersky, present here: https://github.com/eliben/pycparser

This implementation supports the C99 grammar, and has basic actions defined for each of the grammar rules. We had to add rules for generating the graph using DOT (we have used pydot for that purpose), and had to modify most, if not all, of the actions to make them understandable as well as function the way we wanted them to. The original author includes a lot of code that helps generate abstract syntax tree in text, but we have removed the focus from AST and worked to make the dot graph as accurate as possible.

Steps to build and run the parser:
-----------------------------------

cd /src/examples
python using_cpp_libc.py -f ../../tests/c_files/c_files/<filename> -g <graphname>

where <filename> is the name of the C file that we want to parse. The dot graph will be generated in /src/examples as <graphname>. 