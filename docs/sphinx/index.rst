.. ReqManTool documentation master file, created by
   sphinx-quickstart on Mon Oct  9 2023.
   You can adapt this file completely to your liking, but it should at least
   contain the root `toctree` directive.

ReqManTool Documentation
========================

**ReqManTool** is a local-first, DOORS-inspired requirements management tool for small teams —
projects, modules, hierarchical requirements, custom attributes, directional
traceability links, and ReqIF/CSV/XLSX/DOCX export with ReqIF/CSV import.

.. image:: https://img.shields.io/badge/version-0.1.0-blue
   :target: https://github.com/bula173/ReqManTool/releases
   :alt: Version

.. image:: https://img.shields.io/badge/license-AGPL--3.0--only-orange
   :target: https://github.com/bula173/ReqManTool/blob/main/LICENSE
   :alt: License

.. image:: https://img.shields.io/badge/python-3.11%2B-blue
   :target: https://www.python.org/downloads/
   :alt: Python Version

.. image:: https://img.shields.io/badge/EN_50128-T2%20Tool-brightgreen
   :target: quality/requirements/TOOL_REQUIREMENTS.md
   :alt: EN 50128 T2 Tool

---

.. toctree::
   :maxdepth: 2
   :caption: Getting Started

   quickstart
   installation
   configuration

.. toctree::
   :maxdepth: 2
   :caption: User Guide

   projects
   modules
   requirements
   attributes
   traceability
   baselines
   reports
   import_export
   templates

.. toctree::
   :maxdepth: 2
   :caption: Server Edition

   server/installation
   server/authentication
   server/permissions
   server/audit

.. toctree::
   :maxdepth: 2
   :caption: API Reference

   api/rest
   api/websocket
   api/models

.. toctree::
   :maxdepth: 2
   :caption: Development

   contributing
   architecture
   testing

.. toctree::
   :maxdepth: 1
   :caption: Compliance

   compliance/t2_qualification
   compliance/en50128

Indices and tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`