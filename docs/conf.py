# Configuration file for the Sphinx documentation builder.
#
# For the full list of built-in configuration values, see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

# -- Project information -----------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#project-information

project = "Infrastructure Systems"
copyright = "2024, NREL"
author = "NREL"
release = "0.1.0"

# -- General configuration ---------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#general-configuration

extensions = [
    "myst_parser",
    "sphinx.ext.githubpages",
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.todo",
    "sphinx_copybutton",
    "sphinxcontrib.autodoc_pydantic",
    "sphinxcontrib.mermaid",
    "sphinx_tabs.tabs",
]

templates_path = ["_templates"]
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]


# -- Options for HTML output -------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#options-for-html-output

source_suffix = {
    ".txt": "markdown",
    ".md": "markdown",
}

html_theme = "furo"
html_title = "Infrastructure Systems Documentation"
html_theme_options = {
    "navigation_with_keys": True,
}

todo_include_todos = True
autoclass_content = "both"
autodoc_member_order = "bysource"
todo_include_todos = True
copybutton_only_copy_prompt_lines = True
copybutton_exclude = ".linenos, .gp, .go"
copybutton_line_continuation_character = "\\"
copybutton_here_doc_delimiter = "EOT"
copybutton_prompt_text = "$"
copybutton_copy_empty_lines = False

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
    "dateutil": ("https://dateutil.readthedocs.io/en/stable/", None),
}
