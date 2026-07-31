import tempfile
from pathlib import Path

from generate_examples import EXAMPLES_OUT as REPO_EXAMPLES_DIR
from generate_examples import main as gen_examples_main


def read_text_universal(path: Path) -> str:
    """Read file with universal newline mode (all line endings converted to LF)."""
    with open(path, encoding="utf-8", newline=None) as f:
        return f.read()


def check_examples(output_directory: Path) -> None:
    gen_examples_main(output_path=output_directory)
    errors = []
    checked_files = []

    # Check generated files exist in repo and match content
    for generated_file in sorted(output_directory.rglob("*")):
        if generated_file.is_dir():
            continue

        rel_path = generated_file.relative_to(output_directory)
        repo_file = REPO_EXAMPLES_DIR / rel_path

        if not repo_file.exists():
            errors.append(f"Generated file does not exist in repo: {rel_path}")
            continue

        try:
            generated_content = read_text_universal(generated_file)
            repo_content = read_text_universal(repo_file)
        except Exception as e:
            errors.append(f"Error reading {rel_path}: {e}")
            continue

        checked_files.append(rel_path)

        if generated_content != repo_content:
            errors.append(f"Content mismatch: {rel_path}")

    # Check for files in repo that weren't generated
    for repo_file in sorted(REPO_EXAMPLES_DIR.rglob("*")):
        if repo_file.is_dir():
            continue
        rel_path = repo_file.relative_to(REPO_EXAMPLES_DIR)
        generated_file = output_directory / rel_path
        if not generated_file.exists():
            errors.append(f"Expected generated file missing: {rel_path}")

    # Report all errors
    for error in errors:
        print(f"ERROR: {error}")

    print(f"Checked {len(checked_files)} example files against the repository working directory")
    if errors:
        print(f"\n{len(errors)} file(s) differ from generated output — run 'just fix' or `just gen-examples`")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as temp_dir:
        TEMP_EXAMPLE_DIRECTORY = Path(temp_dir)
        check_examples(TEMP_EXAMPLE_DIRECTORY)
