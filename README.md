# modm-data: Embedded Hardware Description

This project is a collection of data processing pipelines that convert and
combine multiple sources of hardware description data into the most accurate
common representation without manual supervision.

There are many different supported input sources per hardware vendor:

- PDF technical documentation, especially datasheets and reference manuals.
- Source code and CMSIS-SVD files describing peripheral registers.
- Vendor libraries for helping with naming things canonically.
- Proprietary databases extracted from vendor tooling.

These input sources are made accessible via deterministic data pipelines before
finally merging them together. This approach has the best chance of
compensating weaknesses in each individual input source while also arbitrating
conflicts. The output formats are knowledge graphs with a shared ontology.

The resulting knowledge graphs represent a normalized and complete semantic
description of the hardware and are NOT intended to be used directly. Rather,
you should extract the data you require and convert it into a format that is
useful for your specific use case and device scope. This repository only
contains data pipeline code, therefore, if you are interested in the hardware
description data only, please use the resulting knowledge graphs directly.

> **Warning**  
> The project is still in beta and not fully functional or documented.
> Improving the documentation and flexibility of the `modm_data.pdf2html`
> submodule is the main focus of development right now.
> No output data other than HTML is currently supported.


## Installation

You can install this Python ≥3.11 project via PyPi:

```sh
pip install modm-data
```

You also need `g++` and `patch` installed and callable in your path.


## Input Sources

You can download all input sources via `make input-sources`. Please note that it
may take a while to download ~10GB of data, mostly PDF technical documentation.

This project uses only publicly available data sources which we have aggregated
in several GitHub repositories. However, since the copyright of some sources
prohibits republication, these sources are downloaded from the vendor websites
directly:

- STMicro CubeMX database.
- STMicro PDF technical documentation.


## Pipelines

The data pipelines are implemented as Python modules inside `modm_data` folder and
have the following structure:

```mermaid
flowchart LR
    A(PDF) -->|pdf2html| B
    B -->|html2svd| D
    B(HTML) -->|html| C
    %% C --> K
    C(Python) -->|owl| E
    D(CMSIS-SVD) -->|cmsis-svd| C
    E[OWL]
    F(CMSIS\nHeader) -->|header2svd| D
    G(CubeMX) -->|cubemx| C
    H(CubeHAL) -->|cubehal| C
    J -->|dl| A
    J -->|dl| F
    J -->|dl| G
    J -->|dl| H
    J[Vendor] -->|dl| D
    %% K[Evaluation]
```

Each pipeline has its own command-line interface, please refer to the API
documentation for their advanced usage.


## Development

For development you can install the package locally:

```sh
pip install -e ".[all]"
```

To browse the API documentation locally:

```sh
pdoc modm_data
```


## Citation

This project is a further development of a [peer-reviewed paper published in
the in the Journal of Systems Research (JSys)](todo).
Please cite this paper when referring to this project:

```bib
@article{hauser2023automatically,
  title={{Automatically Extracting Hardware Descriptions from PDF Technical Documentation}},
  author={Hauser, Niklas and Pennekamp, Jan},
  journal={Journal of Systems Research (JSys)},
  volume={3},
  issue={2},
  year={2023},
  doi={10.5070/tbd}
}
```

The paper itself is based on a [master thesis](https://salkinium.com/master.pdf).
