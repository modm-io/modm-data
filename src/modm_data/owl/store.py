# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

from ..utils import ext_path
import owlready2 as owl
import lxml.etree as ET
from pathlib import Path


XSLT_SORT = r"""
<xsl:stylesheet xmlns:xsl="http://www.w3.org/1999/XSL/Transform" version="1.0"
                xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
                xmlns:xsd="http://www.w3.org/2001/XMLSchema#"
                xmlns:rdfs="http://www.w3.org/2000/01/rdf-schema#"
                xmlns:owl="http://www.w3.org/2002/07/owl#">

  <xsl:template match="/">
    <xsl:apply-templates/>
  </xsl:template>

  <xsl:template match="owl:Ontology">
    <xsl:copy>
      <!-- Sort the attributes by name. -->
      <xsl:for-each select="Package">
        <xsl:sort select="@rdf:resource(.)"/>
        <xsl:copy/>
      </xsl:for-each>
      <xsl:apply-templates/>
    </xsl:copy>
  </xsl:template>

  <xsl:template match="text()|comment()|processing-instruction()">
    <xsl:copy/>
  </xsl:template>

</xsl:stylesheet>
"""


class Store:
    def __init__(self, vendor, device):
        self.base_url = "https://data.modm.io"
        self.vendor = vendor
        self.device = device
        self._path = ext_path(f"{vendor}/owl")
        # Add the directory to the search path
        owl.onto_path.append(self._path)
        self.ontology = owl.get_ontology(f"{self.base_url}/{vendor}/{device}")

    # def set_device_iri(self, device):
    #     self.ontology.set_base_iri(f"{self.base_url}/{self.vendor}/{device}", rename_entities=False)

    def namespace(self, name):
        return self.ontology.get_namespace(f"{self.vendor}/{name}")

    def load(self, name=None):
        if name is None: name = "ontology"
        fileobj = open(self._path / f"{name}.owl", "rb")
        self.ontology.load(only_local=True, fileobj=fileobj, reload=True)

    def save(self, name=None):
        self._path.mkdir(exist_ok=True, parents=True)
        if name is None: name = "ontology"
        file = str(self._path / f"{name}.owl")
        self.ontology.save(file=file)

        # dom = ET.parse(file)
        # xslt = ET.XML(XSLT_SORT)
        # transform = ET.XSLT(xslt)
        # newdom = transform(dom)
        # Path(file).write_bytes(ET.tostring(newdom, pretty_print=True))

        # owl.default_world.set_backend(filename=str(self._path / f"{name}.sqlite3"))
        # owl.default_world.save()

    def clear(self):
        # self.ontology._destroy_cached_entities()
        self.ontology.world._destroy_cached_entities()
        # self.ontology.destroy()
        # self.ontology = owl.get_ontology(f"{self.base_url}/{self.vendor}")

    def __repr__(self) -> str:
        return f"Store({self.vendor}/{self.device})"

