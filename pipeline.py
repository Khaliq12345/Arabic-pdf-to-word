import argparse
import glob
import os
import sys
from datetime import datetime
from google import genai
from google.genai import types
from docx import Document
from docxcompose.composer import Composer
from pdf2image import convert_from_bytes, convert_from_path
import subprocess
from dotenv import load_dotenv

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
client = genai.Client(api_key=GEMINI_API_KEY)


def md_to_docx(markdown_file: str, word_file: str):
    subprocess.run(
        [
            "pandoc",
            markdown_file,
            "-o",
            word_file,
            "-M",
            "lang=ar",
            "--reference-doc=arabic-template.docx",
        ]
    )


def split_pdf(file_path=None, binary_data=None, dpi=300):
    """Step 1: Convert PDF pages into PNG images stored in a unique folder."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_folder = f"pdf_processing_{timestamp}"
    os.makedirs(output_folder, exist_ok=True)

    print(f"[*] Extracting PDF pages to: {output_folder}")

    if file_path:
        pages = convert_from_path(file_path, dpi=dpi)
    elif binary_data:
        pages = convert_from_bytes(binary_data, dpi=dpi)
    else:
        raise ValueError("No PDF input provided.")

    # Save pages with zero-padding (e.g., page_01, page_02) so they sort correctly later
    padding_length = len(str(len(pages)))
    for i, page in enumerate(pages):
        page_num = str(i + 1).zfill(max(2, padding_length))
        image_path = os.path.join(output_folder, f"page_{page_num}.png")
        page.save(image_path, "PNG")

    print(f"[+] Extracted {len(pages)} pages.")
    return output_folder


def image_to_markdown(
    image_path: str, model: str = "gemini-3.1-flash-lite"
) -> str | None:
    """Helper: Use Gemini API to extract Arabic text to Markdown."""
    with open(image_path, "rb") as f:
        image_bytes = f.read()

    prompt = (
        "The image contains Arabic text. Extract ALL content exactly as it appears "
        "and convert it into well-structured Markdown.\n\n"
        "Critical accuracy requirements:\n"
        "- Preserve Arabic text exactly, including diacritics (tashkeel) if present.\n"
        "- Numbers are extremely important: copy all numbers (Arabic-Indic ٠١٢٣٤٥٦٧٨٩ "
        "or Western 0123456789, as they appear) exactly as written. Do not round, "
        "estimate, reformat, or convert number systems. Double-check every digit, "
        "decimal point, comma, and percentage sign before finalizing.\n"
        "- Preserve the original right-to-left reading order of lines, sentences, "
        "and table cells.\n"
        "- Preserve tables using Markdown table syntax, keeping row/column order "
        "and all numeric values intact.\n"
        "- Preserve headings, lists, and emphasis (bold/italic) as structure, not just text.\n"
        "- Do not translate anything. Keep all text in Arabic.\n"
        "- Do not add, omit, or infer any content that is not visibly present in the image.\n"
        "- If a character or number is unclear/ambiguous, mark it as [unclear] rather "
        "than guessing.\n\n"
        "Return only the Markdown output, with no extra commentary or explanation."
    )

    response = client.models.generate_content(
        model=model,
        contents=[
            types.Part.from_bytes(data=image_bytes, mime_type="image/png"),
            prompt,
        ],
        config=types.GenerateContentConfig(temperature=0),
    )
    return response.text


def process_images_to_docx(folder_path: str):
    """Step 2: Process all PNGs in the folder and return list of created DOCX paths."""
    image_paths = sorted(glob.glob(os.path.join(folder_path, "page_*.png")))
    if not image_paths:
        print("[-] No images found to process.")
        return []

    print(f"\n[*] Processing {len(image_paths)} pages through Gemini API...")
    docx_files = []

    for path in image_paths:
        base_name = os.path.splitext(os.path.basename(path))[0]
        markdown_file = os.path.join(folder_path, f"{base_name}.md")
        word_file = os.path.join(folder_path, f"{base_name}.docx")

        print(f"    -> Processing: {os.path.basename(path)}")
        try:
            markdown_output = image_to_markdown(path)
            if markdown_output:
                with open(markdown_file, "w", encoding="utf-8") as f:
                    f.write(markdown_output)

                md_to_docx(markdown_file, word_file)
                docx_files.append(word_file)
            else:
                print(f"    [-] No markdown to process {os.path.basename(path)}")
        except Exception as e:
            print(f"    [-] Failed to process {os.path.basename(path)}: {e}")

    return docx_files


def combine_docx_files(docx_files, output_folder):
    """Step 3: Combine all individual Word files into one master document."""
    if not docx_files:
        print("[-] No DOCX files available to combine.")
        return

    print(f"\n[*] Combining {len(docx_files)} Word files into one master document...")

    # Initialize composer with the first document
    master_path = docx_files[0]
    master = Document(master_path)
    composer = Composer(master)

    # Append the rest of the documents
    for next_doc_path in docx_files[1:]:
        next_doc = Document(next_doc_path)
        composer.append(next_doc)

    # Save final combined document in the root of the output folder
    final_output_path = os.path.join(output_folder, "combined_final.docx")
    composer.save(final_output_path)
    print(f"[+] Done! Final combined file saved to: {final_output_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Full Pipeline: PDF -> Images -> OCR -> Merged Word Doc."
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("-f", "--file", help="Path to the PDF file")
    group.add_argument(
        "-b",
        "--binary",
        action="store_true",
        help="Read PDF as binary stream from stdin",
    )

    parser.add_argument(
        "--dpi", type=int, default=300, help="DPI for conversion (default: 300)"
    )
    args = parser.parse_args()

    # Determine input data source
    file_input = args.file
    binary_input = None

    if args.binary:
        print("[*] Reading PDF from standard input stream...")
        binary_input = sys.stdin.buffer.read()
        if not binary_input:
            print("[-] Error: Received empty binary stream.", file=sys.stderr)
            sys.exit(1)

    try:
        # Run entire pipeline
        target_folder = split_pdf(
            file_path=file_input, binary_data=binary_input, dpi=args.dpi
        )
        created_docx_list = process_images_to_docx(target_folder)
        combine_docx_files(created_docx_list, target_folder)

    except Exception as e:
        print(f"\n[-] Pipeline terminated unexpectedly: {e}", file=sys.stderr)
