"""La verdad del procesador de archivos: un fallo se dice, y lo escrito se deshace.

QUE PRUEBA ESTE FICHERO
    `actions/file_processor.py` tiene tres rutas de escritura que el resto del
    producto no tenía:

    1. Los manejadores respondían a un fallo con una frase ("Convert failed:
       cannot identify image file") y el despachador vestía **toda** respuesta
       no vacía como `status=done ... verified=true`. El modelo recibía, en la
       línea que el producto usa para decidir, que una conversión había
       funcionado cuando no había funcionado.
    2. La salida se escribía encima de lo que hubiera, sin mirar: una
       conversión podía borrar un fichero que el usuario todavía tenía.
    3. Nada de lo que escribía el procesador entraba en la pila de `undo`, así
       que la tabla de clases ("una escritura local es silenciosa, registrada
       y **reversible**") era falsa para esta herramienta.

    Estas pruebas fijan las tres cosas.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from actions import file_processor
from core import undo


class FileProcessorTruthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(self.temp_dir)
        undo.clear()

    def tearDown(self) -> None:
        undo.clear()

    # ── 1. Un fallo es un fallo ─────────────────────────────────────────────
    def test_a_failed_conversion_is_reported_as_failed(self) -> None:
        broken = self.root / "broken.png"
        broken.write_bytes(b"this is not an image")

        result = file_processor.file_processor({
            "file_path": str(broken),
            "action": "convert",
            "format": "jpg",
        })

        self.assertIn("status=failed", result)
        self.assertNotIn("status=done", result)
        # El motivo llega al modelo: no basta con decir "falló".
        self.assertIn("Convert failed", result)

    def test_a_failed_run_is_reported_as_failed(self) -> None:
        broken = self.root / "broken.json"
        broken.write_text("{not json", encoding="utf-8")

        result = file_processor.file_processor({
            "file_path": str(broken),
            "action": "validate",
        })

        self.assertIn("status=failed", result)
        self.assertNotIn("status=done", result)

    # ── 2. No se pisa lo que el usuario tenía ───────────────────────────────
    def test_an_existing_output_is_never_overwritten(self) -> None:
        source = self.root / "source.json"
        source.write_text('{"a": 1}', encoding="utf-8")
        output = self.root / "source_formatted.json"
        output.write_text("CONTENT OF THE USER", encoding="utf-8")

        result = file_processor.file_processor({
            "file_path": str(source),
            "action": "format",
        })

        self.assertIn("status=failed", result)
        self.assertEqual(
            output.read_text(encoding="utf-8"),
            "CONTENT OF THE USER",
            "el fichero del usuario no puede cambiar de contenido",
        )

    # ── 3. Una extracción que no ocurre no deja carpeta ─────────────────────
    def test_a_failed_archive_extraction_leaves_no_destination(self) -> None:
        archive = self.root / "sample.zip"
        archive.write_bytes(b"not an archive at all")
        destination = self.root / "extracted"

        result = file_processor.file_processor({
            "file_path": str(archive),
            "action": "extract",
            "destination": str(destination),
        })

        self.assertIn("status=failed", result)
        self.assertFalse(
            destination.exists(),
            "una extracción que no ocurrió no puede dejar una carpeta detrás",
        )

    # ── 4. Lo que se escribe, se deshace ────────────────────────────────────
    def test_a_successful_write_is_registered_as_undoable(self) -> None:
        source = self.root / "source.json"
        source.write_text('{"a": 1}', encoding="utf-8")

        result = file_processor.file_processor({
            "file_path": str(source),
            "action": "format",
        })

        self.assertIn("status=done", result)
        output = self.root / "source_formatted.json"
        self.assertTrue(output.exists())
        self.assertTrue(undo.can_undo(), "una escritura local tiene que ser reversible")

        undone = undo.undo_last()

        self.assertIn("source_formatted.json", undone)
        self.assertFalse(output.exists(), "deshacer tiene que quitar lo que se escribió")

    # ── 5. Ni una traza en la consola ───────────────────────────────────────
    def test_an_unexpected_exception_prints_no_traceback(self) -> None:
        source = self.root / "source.json"
        source.write_text('{"a": 1}', encoding="utf-8")

        with patch.object(
            file_processor, "_process_json",
            side_effect=RuntimeError("boom with a private detail"),
        ):
            buffer = StringIO()
            with redirect_stdout(buffer):
                result = file_processor.file_processor({
                    "file_path": str(source),
                    "action": "format",
                })

        printed = buffer.getvalue()
        self.assertNotIn("Traceback", printed)
        self.assertIn("status=failed", result)

    # ── 6. El contrato de salida no cambia para lo que sí funciona ─────────
    def test_a_successful_read_still_reports_done_and_verified(self) -> None:
        source = self.root / "source.json"
        source.write_text(json.dumps({"a": 1}), encoding="utf-8")

        result = file_processor.file_processor({
            "file_path": str(source),
            "action": "validate",
        })

        self.assertIn("status=done", result)
        self.assertIn("verified=true", result)


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
