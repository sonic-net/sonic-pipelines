# CodeOwners Generator

The script to run over the local copy of the GitHub repo, validate all contributors against
the GitHub and list the top contributors per folder in the CODEOWNERS per folder.

## Building the Wheel Package

May need to do it from the virtual environment (see how to setup below)
To build the wheel package, use the following command:

```bash
pip3 install build
python3 -m build
```

This will create both a source distribution (`.tar.gz`) and a wheel distribution (`.whl`) in the `dist/` directory.

## Setting Up Virtual Environment

### Option 1: Using venv (Recommended)

```bash
# Create a virtual environment
python3 -m venv venv

# Activate the virtual environment
# On macOS/Linux:
source venv/bin/activate

# On Windows:
# venv\Scripts\activate
```

### Option 2: Using conda

```bash
# Create a conda environment
conda create -n codeowners python=3.8

# Activate the conda environment
conda activate codeowners
```

## Installation

### Install from Wheel Package

After building the wheel package, install it in your virtual environment:

```bash
# Make sure your virtual environment is activated
pip install dist/*.whl
```

### Install in Development Mode

For development purposes, you can install the package in editable mode:

```bash
# Make sure your virtual environment is activated
pip install -e .
```

### Updating the GitHub tokens

Without GitHub tokens the script will be only allowed 60 API requests
per hour. For initial runs or larger repos, recommended to provide the GitHub tokens 

Request a GitHub token(s) with access to public repositories (default setting).

https://github.com/settings/personal-access-tokens

(GitHub->Profile->Settings(Gear icon)->Developer Settings->Personalized Access Tokens->Fine Grained Tokens)

Several tokens are recommended for larger repo. One token allows to process about 20000 commits per hour, depending on how many unique/new contributors are in the repo
Pass the tokens as a comma-separated environment variable.
```shell
# example
env GITHUB_API_TOKENS = "github_pat_XXXXX,github_pat_YYYYY,github_pat_ZZZZZZ" codeowners-cli ....
```


## Running the CLI

After installation, you can use the `codeowners-cli` command:

```bash
# Basic usage
codeowners-cli --help

# Example: Analyze a repository
codeowners-cli --repo_name /path/to/your/repo_name --contributors-file contributors.yaml

# For asynchronous (asyncio) version also available
codeowners-async-cli 
```

## CLI Options

The `codeowners-cli` and `codeowners-async-cli` commands supports various options:

- `--repo`: Path to the local Git repository
- `--contributors_file`: Path to the contributors YAML file
- `--folder_presets_file`: YAML file with the preset folder information
- `--active_after`: Date from which to consider contributors active (YYYY-MM-DD)
- `--max_owners`: Maximum number of owners per folder
- `--log_level`: Log level of the output

## Maintenance

After the first run or when the new ```contributors.yaml``` is created
review emails for the record ```github_id: -1```, if any of them match the 
known contributor elsewhere in the file, move them to the ```email:``` list 
of that user. Rerun the script.
__Be careful not to duplicate emails.__

## Requirements

- Python 3.8 or higher
- Git repository with commit history
- Contributors YAML file
- GitHub API access (for contributor validation)

## Dependencies

- requests >= 2.3.4
- PyYAML >= 6.0.2
- aiofiles >= 24.1.0
- aiohttp >= 3.10.11
- Brotli >= 1.1.0 (optional)
- aiodns >= 3.2.0 (optional)
