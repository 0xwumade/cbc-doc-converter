#!/usr/bin/env python3
"""
Flask Web Server for CBC Document Converter
Provides REST API endpoints for the web UI to communicate with the Python converter
"""

from flask import Flask, request, send_file, jsonify
from flask_cors import CORS
from werkzeug.utils import secure_filename
import os
import tempfile
import json
from pathlib import Path
import logging

# Try to import converter - if it fails, provide helpful error
try:
    from converter import (
        pdf_to_docx, docx_to_pdf,
        find_libreoffice
    )
    from config import ConverterConfig
    CONVERTER_AVAILABLE = True
except ImportError as e:
    print(f"Warning: Could not import converter modules: {e}")
    CONVERTER_AVAILABLE = False
    ConverterConfig = None

# Flask app setup
app = Flask(__name__)

CORS(app)

# Configuration
UPLOAD_FOLDER = tempfile.gettempdir()
ALLOWED_EXTENSIONS = {'pdf', 'docx', 'doc'}

try:
    configured_max_file_size_mb = int(os.environ.get('MAX_FILE_SIZE_MB', ''))
except ValueError:
    configured_max_file_size_mb = 0

# Vercel serverless requests have a much smaller practical body limit than a
# long-running Flask server, so keep deployed uploads below that ceiling.
MAX_FILE_SIZE_MB = configured_max_file_size_mb or (4 if os.environ.get('VERCEL') else 50)
MAX_FILE_SIZE = MAX_FILE_SIZE_MB * 1024 * 1024

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = MAX_FILE_SIZE

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Try to load converter config
converter_config = None
if CONVERTER_AVAILABLE and ConverterConfig:
    try:
        converter_config = ConverterConfig()
    except:
        converter_config = None

# No file storage - files are converted and sent directly to user


def allowed_file(filename):
    """Check if file extension is allowed."""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def get_file_extension(mode):
    """Get output file extension based on conversion mode."""
    if mode == 'pdf-to-word':
        return '.docx'
    elif mode == 'word-to-pdf':
        return '.pdf'
    return '.bin'


def get_conversion_function(input_ext, output_ext):
    """Get the appropriate conversion function."""
    converters = {
        ('pdf', 'docx'): pdf_to_docx,
        ('docx', 'pdf'): docx_to_pdf,
        ('doc', 'pdf'): docx_to_pdf,
    }
    return converters.get((input_ext, output_ext))


@app.route('/')
def index():
    """Serve the main converter page."""
    html_path = os.path.join(os.path.dirname(__file__), 'index.html')
    return send_file(html_path, mimetype='text/html')


@app.route('/styles.css')
def serve_styles():
    """Serve CSS stylesheet."""
    css_path = os.path.join(os.path.dirname(__file__), 'styles.css')
    if not os.path.exists(css_path):
        logger.error(f"CSS file not found at: {css_path}")
        return "CSS file not found", 404
    try:
        return send_file(css_path, mimetype='text/css')
    except Exception as e:
        logger.error(f"Error serving CSS: {e}")
        return f"Error serving CSS: {e}", 500


@app.route('/script.js')
def serve_script():
    """Serve JavaScript file."""
    js_path = os.path.join(os.path.dirname(__file__), 'script.js')
    if not os.path.exists(js_path):
        logger.error(f"JS file not found at: {js_path}")
        return "JS file not found", 404
    try:
        return send_file(js_path, mimetype='application/javascript')
    except Exception as e:
        logger.error(f"Error serving JS: {e}")
        return f"Error serving JS: {e}", 500


@app.route('/cbc-logo.svg')
def serve_logo():
    """Serve CBC logo."""
    logo_path = os.path.join(os.path.dirname(__file__), 'cbc-logo.svg')
    if os.path.exists(logo_path):
        return send_file(logo_path, mimetype='image/svg+xml')
    else:
        # Return placeholder if logo doesn't exist
        placeholder = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 200">
            <circle cx="100" cy="100" r="100" fill="#0066CC"/>
            <text x="100" y="135" text-anchor="middle" fill="white" font-size="80" font-weight="bold" font-family="Arial">C</text>
        </svg>'''
        return placeholder, 200, {'Content-Type': 'image/svg+xml'}


@app.route('/cbc-logo.png')
def serve_logo_png():
    """Serve CBC logo PNG."""
    logo_path = os.path.join(os.path.dirname(__file__), 'cbc-logo.png')
    if os.path.exists(logo_path):
        return send_file(logo_path, mimetype='image/png')
    else:
        logger.error(f"PNG logo file not found at: {logo_path}")
        return "Logo not found", 404


@app.route('/api/health')
def health_check():
    """Health check endpoint."""
    libreoffice_found = False
    if CONVERTER_AVAILABLE and 'find_libreoffice' in globals():
        libreoffice_found = find_libreoffice() is not None
    
    return jsonify({
        'status': 'healthy',
        'version': '1.0.0',
        'service': 'CBC Document Converter',
        'converterAvailable': CONVERTER_AVAILABLE,
        'libreofficeFound': libreoffice_found
    })


@app.route('/api/config')
def get_config():
    """Get current configuration."""
    config_data = {}
    if converter_config:
        try:
            config_data = converter_config.get_all()
        except:
            config_data = {}
    
    return jsonify({
        'config': config_data,
        'maxFileSize': MAX_FILE_SIZE,
        'maxFileSizeMb': MAX_FILE_SIZE_MB,
        'platform': 'vercel' if os.environ.get('VERCEL') else 'local',
        'supportedFormats': {
            'pdf-to-word': 'Convert PDF to Word (DOCX)',
            'word-to-pdf': 'Convert Word to PDF'
        }
    })


@app.route('/api/convert', methods=['POST'])
def convert_file():
    """
    Main conversion endpoint
    
    Expected form data:
    - file: The file to convert
    - mode: Conversion mode (pdf-to-word, word-to-pdf)
    - preserveFormatting: Whether to preserve formatting (true/false)
    - compress: Whether to compress output (true/false)
    """
    
    if not CONVERTER_AVAILABLE:
        return jsonify({'error': 'Converter not available - converter.py import failed'}), 503
    
    try:
        # Check if file is in request
        if 'file' not in request.files:
            logger.error('No file provided in request')
            return jsonify({'error': 'No file provided'}), 400

        file = request.files['file']

        # Check if file is selected
        if file.filename == '':
            logger.error('No file selected')
            return jsonify({'error': 'No file selected'}), 400

        # Validate file
        if not allowed_file(file.filename):
            logger.error(f'Invalid file type: {file.filename}')
            return jsonify({'error': 'Invalid file type. Supported: PDF, DOCX'}), 400

        # Get conversion mode
        mode = request.form.get('mode', 'pdf-to-word')

        logger.info(f"Conversion request: mode={mode}, file={file.filename}")

        # Save uploaded file temporarily
        filename = secure_filename(file.filename)
        input_path = os.path.join(app.config['UPLOAD_FOLDER'], f"input_{filename}")
        file.save(input_path)

        logger.debug(f"Uploaded file saved to: {input_path}")

        # Determine conversion function
        input_ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else 'bin'
        
        if mode == 'pdf-to-word':
            if input_ext != 'pdf':
                return jsonify({'error': 'Please upload a PDF file for PDF→Word conversion'}), 400
            output_ext = 'docx'
            converter_func = pdf_to_docx
        elif mode == 'word-to-pdf':
            if input_ext not in ['docx', 'doc']:
                return jsonify({'error': 'Please upload a Word document for Word→PDF conversion'}), 400
            output_ext = 'pdf'
            converter_func = docx_to_pdf
        else:
            return jsonify({'error': f'Unsupported conversion mode: {mode}'}), 400

        # Generate output filename
        output_filename = f"{Path(filename).stem}_converted.{output_ext}"
        output_path = os.path.join(app.config['UPLOAD_FOLDER'], output_filename)

        logger.debug(f"Output will be saved to: {output_path}")

        # Perform conversion
        logger.info(f"Starting conversion: {input_ext} → {output_ext}")
        
        try:
            if converter_config:
                result = converter_func(input_path, output_path, converter_config)
            else:
                result = converter_func(input_path, output_path)
        except Exception as conv_error:
            logger.error(f"Conversion error: {str(conv_error)}", exc_info=True)
            
            # Clean up input file
            if os.path.exists(input_path):
                try:
                    os.remove(input_path)
                except:
                    pass
            
            return jsonify({
                'error': f'Conversion failed: {str(conv_error)}'
            }), 500

        if not result or not os.path.exists(output_path):
            logger.error("Conversion failed or output file not created")
            
            # Clean up input file
            if os.path.exists(input_path):
                try:
                    os.remove(input_path)
                except:
                    pass
            
            return jsonify({
                'error': 'Conversion failed. Please check the file and try again.'
            }), 500

        logger.info(f"Conversion successful: {output_path}")

        # Get output file size
        output_size = os.path.getsize(output_path)
        logger.debug(f"Output file size: {output_size} bytes")

        # Read file into memory
        with open(output_path, 'rb') as f:
            file_data = f.read()
        
        # Clean up temporary files immediately
        try:
            if os.path.exists(input_path):
                os.remove(input_path)
            if os.path.exists(output_path):
                os.remove(output_path)
            logger.debug("Temporary files cleaned up")
        except Exception as cleanup_error:
            logger.warning(f"Failed to clean up temporary files: {cleanup_error}")
        
        # Return file directly for download
        logger.info(f"Sending file to user: {output_filename}")
        from io import BytesIO
        return send_file(
            BytesIO(file_data),
            mimetype='application/octet-stream',
            as_attachment=True,
            download_name=output_filename
        )

    except Exception as e:
        logger.error(f"Conversion error: {str(e)}", exc_info=True)
        return jsonify({
            'error': f'Conversion error: {str(e)}'
        }), 500




    except Exception as e:
        logger.error(f"Download error: {str(e)}", exc_info=True)
        return jsonify({'error': f'Download error: {str(e)}'}), 500


@app.route('/api/validate', methods=['POST'])
def validate_file():
    """Validate a file before conversion."""
    try:
        if 'file' not in request.files:
            return jsonify({'valid': False, 'error': 'No file provided'}), 400

        file = request.files['file']

        # Check file extension
        if not allowed_file(file.filename):
            return jsonify({
                'valid': False,
                'error': 'Invalid file type. Supported types: PDF, DOCX, DOC, PPTX'
            }), 400

        # Check file size
        file.seek(0, os.SEEK_END)
        file_size = file.tell()
        file.seek(0)

        if file_size > MAX_FILE_SIZE:
            return jsonify({
                'valid': False,
                'error': f'File is too large. Maximum size is {MAX_FILE_SIZE / 1024 / 1024:.0f}MB'
            }), 400

        logger.info(f"File validation successful: {file.filename}")

        return jsonify({
            'valid': True,
            'filename': secure_filename(file.filename),
            'fileSize': file_size
        }), 200

    except Exception as e:
        logger.error(f"Validation error: {str(e)}")
        return jsonify({
            'valid': False,
            'error': f'Validation error: {str(e)}'
        }), 500


@app.route('/api/batch', methods=['POST'])
def batch_convert():
    """Handle batch conversions (multiple files)."""
    try:
        if 'files' not in request.files:
            return jsonify({'error': 'No files provided'}), 400

        files = request.files.getlist('files')
        mode = request.form.get('mode', 'pdf-to-word')

        logger.info(f"Batch conversion request: {len(files)} files, mode={mode}")

        results = []
        for file in files:
            if not allowed_file(file.filename):
                logger.warning(f"Skipping invalid file: {file.filename}")
                results.append({
                    'filename': file.filename,
                    'status': 'skipped',
                    'error': 'Invalid file type'
                })
                continue

            # Perform conversion
            filename = secure_filename(file.filename)
            input_path = os.path.join(app.config['UPLOAD_FOLDER'], f"input_{filename}")
            file.save(input_path)

            # Get conversion function and perform conversion
            input_ext = filename.rsplit('.', 1)[1].lower()
            
            if mode == 'pdf-to-word':
                output_ext = 'docx'
            elif mode == 'word-to-pdf':
                output_ext = 'pdf'
            else:
                output_ext = 'bin'

            output_filename = f"{Path(filename).stem}_converted.{output_ext}"
            output_path = os.path.join(app.config['UPLOAD_FOLDER'], output_filename)

            try:
                converter_func = get_conversion_function(input_ext, output_ext)
                if converter_func:
                    result = converter_func(input_path, output_path, converter_config)
                    if result:
                        results.append({
                            'filename': filename,
                            'status': 'success',
                            'outputFilename': output_filename
                        })
                    else:
                        results.append({
                            'filename': filename,
                            'status': 'failed',
                            'error': 'Conversion failed'
                        })
                else:
                    results.append({
                        'filename': filename,
                        'status': 'failed',
                        'error': 'Unsupported conversion'
                    })
            except Exception as e:
                logger.error(f"Batch conversion error for {filename}: {str(e)}")
                results.append({
                    'filename': filename,
                    'status': 'failed',
                    'error': str(e)
                })

        logger.info(f"Batch conversion completed: {len([r for r in results if r['status'] == 'success'])} successful")

        return jsonify({
            'status': 'completed',
            'results': results,
            'successCount': len([r for r in results if r['status'] == 'success']),
            'totalCount': len(results)
        }), 200

    except Exception as e:
        logger.error(f"Batch conversion error: {str(e)}", exc_info=True)
        return jsonify({'error': str(e)}), 500





@app.errorhandler(413)
def request_entity_too_large(error):
    """Handle file too large error."""
    return jsonify({
        'error': f'File is too large. Maximum size is {MAX_FILE_SIZE / 1024 / 1024:.0f}MB'
    }), 413


@app.errorhandler(404)
def not_found(error):
    """Handle 404 errors."""
    return jsonify({'error': 'Endpoint not found'}), 404


@app.errorhandler(500)
def internal_error(error):
    """Handle 500 errors."""
    logger.error(f"Internal server error: {str(error)}", exc_info=True)
    return jsonify({'error': 'Internal server error'}), 500


if __name__ == '__main__':
    logger.info("Starting CBC Document Converter Web Server")
    logger.info(f"Upload folder: {UPLOAD_FOLDER}")
    logger.info(f"Max file size: {MAX_FILE_SIZE / 1024 / 1024:.0f}MB")
    
    # Run Flask app
    app.run(
        host='0.0.0.0',
        port=5000,
        debug=False,
        use_reloader=False
    )
