#!/usr/bin/env python3
"""
Extract peripheral register definitions from STM32 CMSIS headers.

This script scans CMSIS header files and extracts register mask definitions for a configurable
peripheral (RCC, GPIO, USART, etc.), then generates an HTML table showing which registers are
available in which device variants.

Usage:
    python tools/scripts/compare_registers.py <path_to_include_folder> [--output output.html] [--prefix RCC]

Examples:
    # Extract RCC registers
    python tools/scripts/compare_registers.py ext/stmicro/header/stm32f0xx/Include --prefix RCC

    # Extract ADC registers
    python tools/scripts/compare_registers.py ext/stmicro/header/stm32f0xx/Include --prefix ADC --output adc_regs.html
"""

import argparse
import re
import sys
from pathlib import Path
from collections import defaultdict
from typing import Dict, Set, Tuple, List
from jinja2 import Environment, FileSystemLoader


def extract_registers(header_file: Path, prefix: str) -> Dict[Tuple[str, str], str]:
    """
    Extract all peripheral register and bit field names from a header file with descriptions.

    Args:
        header_file: Path to the CMSIS header file
        prefix: Peripheral prefix to search for (e.g., 'RCC', 'GPIO', 'ADC')

    Returns:
        Dictionary mapping (register, bitfield) tuples to descriptions
    """
    registers = {}
    # Pattern to match the duplicate definition with optional comment
    # #define PREFIX_REG_BITFIELD        PREFIX_REG_BITFIELD_Msk [/*!< description */]
    # Strip trailing underscore from prefix if present since it's part of the delimiter
    pattern = re.compile(
        rf'#define\s+{re.escape(prefix)}_(\w+)_(\w+)\s+'
        rf'{re.escape(prefix)}_\1_\2_Msk(?:\s+/\*!<\s*(.+?)\s*\*/)?'
    )

    try:
        with open(header_file, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                match = pattern.search(line)
                if match:
                    register = match.group(1)
                    bitfield = match.group(2)
                    description = match.group(3) or ''  # Handle optional description
                    registers[(register, bitfield)] = description
    except Exception as e:
        print(f"Warning: Could not read {header_file}: {e}", file=sys.stderr)

    return registers


def scan_include_folder(include_path: Path, prefix: str) -> Dict[str, Dict[Tuple[str, str], str]]:
    """
    Scan all header files in the include folder.

    Args:
        include_path: Path to the Include folder
        prefix: Peripheral prefix to search for (e.g., 'RCC', 'GPIO', 'ADC')

    Returns:
        Dictionary mapping filename to dict of (register, bitfield) tuples to descriptions
    """
    file_registers = {}

    # Find all .h files
    header_files = sorted(include_path.glob('*.h'))

    if not header_files:
        print(f"Warning: No header files found in {include_path}", file=sys.stderr)
        return file_registers

    for header_file in header_files:
        registers = extract_registers(header_file, prefix)
        if registers:
            file_registers[header_file.name] = registers

    return file_registers


def generate_html_table(file_registers: Dict[str, Dict[Tuple[str, str], str]], template_path: Path, peripheral_name: str) -> str:
    """
    Generate HTML table showing register availability across files using Jinja2 template.

    Args:
        file_registers: Dictionary mapping filename to dict of (register, bitfield) tuples to descriptions
        template_path: Path to the Jinja2 template file
        peripheral_name: Name of the peripheral (e.g., 'RCC', 'GPIO', 'ADC')

    Returns:
        HTML string
    """
    if not file_registers:
        return f"<html><body><p>No {peripheral_name} registers found.</p></body></html>"

    # Organize by register and bitfield with descriptions
    register_bitfields = defaultdict(set)
    bitfield_descriptions = {}  # (register, bitfield) -> description
    for file_regs in file_registers.values():
        for (register, bitfield), description in file_regs.items():
            register_bitfields[register].add(bitfield)
            # Store description (use first encountered)
            if (register, bitfield) not in bitfield_descriptions:
                bitfield_descriptions[(register, bitfield)] = description

    # Sort registers and bitfields
    sorted_registers = sorted(register_bitfields.keys())
    sorted_files = sorted(file_registers.keys())

    # Format filenames for display
    formatted_files = []
    for filename in sorted_files:
        formatted = filename.replace('stm32', '').replace('.h', '').replace('xx', '').upper().replace('X', 'x')
        formatted_files.append(formatted)

    # Build data structure for template
    registers_data = []
    for register in sorted_registers:
        bitfields = sorted(register_bitfields[register])
        bitfield_data = []

        for bitfield in bitfields:
            # Check presence in each file
            presence_values = []
            for filename in sorted_files:
                is_present = (register, bitfield) in file_registers[filename]
                presence_values.append(is_present)

            # Create pattern string
            pattern = ''.join('1' if p else '0' for p in presence_values)
            all_present = all(presence_values)

            # Get description for this bitfield
            description = bitfield_descriptions.get((register, bitfield), '')

            bitfield_data.append({
                'name': bitfield,
                'description': description,
                'presence': {
                    'files': presence_values,
                    'pattern': pattern,
                    'all_present': all_present
                }
            })

        registers_data.append((register, bitfield_data))

    # Count totals
    total_bitfields = sum(len(bf) for bf in register_bitfields.values())

    # Setup Jinja2 environment
    env = Environment(loader=FileSystemLoader(template_path.parent))
    template = env.get_template(template_path.name)

    # Render template
    html = template.render(
        peripheral_name=peripheral_name,
        num_registers=len(sorted_registers),
        num_bitfields=total_bitfields,
        num_files=len(sorted_files),
        files=list(enumerate(formatted_files)),
        registers=registers_data
    )

    return html


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='Extract peripheral register definitions from STM32 CMSIS headers.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  # Extract RCC registers (default)
  %(prog)s ext/stmicro/header/stm32f0xx/Include

  # Extract GPIO registers
  %(prog)s ext/stmicro/header/stm32f0xx/Include --prefix GPIO --output gpio_regs.html

  # Extract ADC registers
  %(prog)s ext/stmicro/header/stm32f0xx/Include --prefix ADC --output adc_regs.html

  # Extract USART registers
  %(prog)s ext/stmicro/header/stm32f0xx/Include --prefix USART --output usart_regs.html
        '''
    )

    parser.add_argument(
        'include_path',
        type=Path,
        help='Path to the CMSIS Include folder'
    )

    parser.add_argument(
        '-o', '--output',
        type=Path,
        default=Path('registers.html'),
        help='Output HTML file (default: registers.html)'
    )

    parser.add_argument(
        '-p', '--prefix',
        type=str,
        default='RCC',
        help='Peripheral prefix to search for (e.g., RCC, GPIO, ADC, USART). (default: RCC)'
    )

    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        help='Enable verbose output'
    )

    args = parser.parse_args()

    # Validate inputs
    if not args.include_path.exists():
        print(f"Error: Include path does not exist: {args.include_path}", file=sys.stderr)
        sys.exit(1)

    if not args.include_path.is_dir():
        print(f"Error: Include path is not a directory: {args.include_path}", file=sys.stderr)
        sys.exit(1)


    # Locate template file
    script_dir = Path(__file__).parent
    template_path = script_dir / 'compare_registers.html.in'

    if not template_path.exists():
        print(f"Error: Template file not found: {template_path}", file=sys.stderr)
        sys.exit(1)

    if args.verbose:
        print(f"Scanning {args.include_path} for {args.prefix} registers with prefix '{args.prefix}_'...")

    file_registers = scan_include_folder(args.include_path, args.prefix)

    if not file_registers:
        print(f"Error: No {args.prefix} registers found!", file=sys.stderr)
        sys.exit(1)

    total_defs = sum(len(regs) for regs in file_registers.values())
    num_files = len(file_registers)

    print(f"Found {total_defs} total register definitions across {num_files} files")
    if args.verbose:
        for fname, regs in sorted(file_registers.items()):
            print(f"  {fname}: {len(regs)} registers")

    if args.verbose:
        print(f"Generating HTML table...")

    html_content = generate_html_table(file_registers, template_path, args.prefix)

    with open(args.output, 'w', encoding='utf-8') as f:
        f.write(html_content)

    print(f"HTML table saved to: {args.output}")


if __name__ == '__main__':
    main()
