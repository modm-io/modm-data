# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import re
import sys
import subprocess
from pathlib import Path
from jinja2 import Environment

TABLE_TEMPLATE = \
r"""
<table>
<tr>
{%- for item in items %}
<td align="{% if align is defined %}{{align}}{% else %}center{% endif %}">{% if item.url %}<a href="{{item.url}}">{% endif %}{{item.name}}{% if item.url %}</a>{% endif %}</td>{%- if loop.index % width == 0 %}
</tr><tr>{%- endif -%}
{%- endfor %}
</tr>
</table>

"""

def repopath(path):
    return Path(__file__).parents[2] / path

def run(where, command, stdin=None):
    print(command)
    result = subprocess.run(command, shell=True, cwd=where, input=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return (result.returncode,
            result.stdout.decode("utf-8").strip(" \n"),
            result.stderr.decode("utf-8").strip(" \n"))

def name(raw_name):
    result = []
    raw_name = str(raw_name).replace("_", " ").replace(".", " ").replace(":", " ")
    for part in raw_name.split(" "):
        part = part.upper()
        result.append(part)

    result = "-".join(result)
    return result

def fmt_url(name):
    return str(name).replace(".", "-").replace(":", "-")

def github_url(path):
    return "https://github.com/modm-io/modm-data/tree/main/" + str(path)

def replace(text, key, content):
    return re.sub(r"<!--{0}-->.*?<!--/{0}-->".format(key), "<!--{0}-->{1}<!--/{0}-->".format(key, content), text, flags=re.DOTALL | re.MULTILINE)

def extract(text, key):
    return re.search(r"<!--{0}-->(.*?)<!--/{0}-->".format(key), text, flags=re.DOTALL | re.MULTILINE).group(1)

def format_table(items, width, align=None):
    subs = {"items": items, "width": width}
    if align: subs["align"] = align
    return Environment().from_string(TABLE_TEMPLATE).render(subs)

def template(path_in, path_out, substitutions):
    data = Environment().from_string(path_in.read_text()).render(substitutions)
    path_out.parent.mkdir(parents=True, exist_ok=True)
    path_out.write_text(data)


# All the paths
readme_path = repopath("README.md")
index_in_path = repopath("docs/index.md.in")
pipelines_in_path = repopath("docs/pipeline_overview.md.in")
sources_in_path = repopath("docs/source_overview.md.in")
index_path = repopath("docs/src/index.md")
pipelines_path = repopath("docs/src/pipeline/overview.md")
sources_path = repopath("docs/src/source/overview.md")
changelog_in_paths = repopath("docs/release").glob("20*.md")
changelog_path = repopath("CHANGELOG.md")

# Read the repo README.md and replace these keys
readme = readme_path.read_text()

# extract these keys
links = extract(readme, "links")
pipelines = extract(readme, "pipelines")
sources = extract(readme, "inputsources")
# remove html comments
readme = re.sub(r"((<!--webignore-->.*?<!--/webignore-->)|(<!--links-->.*?<!--/links-->))\n", "", readme, flags=re.DOTALL | re.MULTILINE)
readme = re.sub(r"<!--.*?-->", "", readme)
readme = readme.replace("https://data.modm.io", "")

template(index_in_path, index_path, {"content": readme, "links": links})
template(pipelines_in_path, pipelines_path, {"content": pipelines, "links": links})
template(sources_in_path, sources_path, {"content": sources, "links": links})

# Check git differences and fail
if "-d" in sys.argv:
    differences = run(repopath("."), r"git diff")[1]
    if len(differences):
        subprocess.run("git --no-pager diff", shell=True, cwd=repopath("."))
        print("\nPlease synchronize the modm documentation:\n\n"
              "    $ python3 tools/scripts/synchronize_docs.py\n\n"
              "and then commit the results!")
        exit(1)

exit(0)
