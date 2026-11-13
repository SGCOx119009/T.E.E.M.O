# T.E.E.M.O. — análisis estático de Torvis

## Alcance

Este documento registra observaciones hechas sobre la instalación local de
Torvis ubicada en `C:\Users\sergi\AppData\Local\Programs\Torvis`, sin ejecutar
el binario ni copiar credenciales, certificados, claves de actualización,
datos de usuario o configuración de cuenta.

Torvis.exe mide aproximadamente 274 MiB y contiene el runtime de Python 3.12,
PySide6/Qt, OpenCV, NumPy, ONNX Runtime, audio/video, FastAPI/Uvicorn,
Playwright, pandas, modelos y recursos auxiliares. Las cadenas del ejecutable
identifican una compilación Nuitka. Por tanto, no contiene el código fuente
Python original en una forma directamente recuperable: la reconstrucción de
T.E.E.M.O. será una implementación limpia basada en comportamiento observado,
interfaces y recursos no sensibles.

## Componentes observados

- Interfaz Qt/PySide6 con HUD, holograma/avatar, reactor, métricas, paneles,
  bloqueo de pantalla, onboarding y configuración de proveedores.
- Conversación Gemini y proveedores alternativos de LLM, STT y TTS; aparecen
  módulos para Gemini, OpenAI, Whisper, Piper, ElevenLabs y voz del sistema.
- Entrada de micrófono, audio remoto desde teléfono, subtítulos, wake word,
  detección de actividad y selección de dispositivos.
- Captura de pantalla, cámara, visión, OpenCV y modelos ONNX.
- Acciones de escritorio: abrir aplicaciones, navegador, archivos, volumen,
  ventanas, mensajes, recordatorios y controles del sistema.
- Memoria local, memoria remota/cloud, resumen de sesiones y búsqueda en
  documentos.
- Dashboard web local/remoto, control desde teléfono, QR, autenticación,
  TLS/certificados y relay.
- Plugins para calendario, correo, WhatsApp, Telegram, impresoras, hogar,
  compras, vídeos, paneles, gestos, postura, rostro, mapas y tareas.
- Tareas en segundo plano, investigación, rutinas, monitorización, updater,
  autoinicio, aplicaciones conectadas y asistentes de configuración.

## Correspondencia con MARK LVII

MARK LVII ya contiene una base funcional importante: conversación Gemini Live,
PyQt, avatar, audio, cámara, captura de pantalla, dashboard, memoria,
proveedores, plugins, tareas, investigación, rutinas, recordatorios y control
del escritorio. T.E.E.M.O. conserva esa base en lugar de reemplazarla.

Las áreas que requieren ampliación son principalmente:

1. separación de servicios de conversación, proveedores, memoria y tareas;
2. visión/cámara y modelos locales con límites claros de privacidad;
3. autoinicio, updater y diagnóstico de instalación;
4. dashboard/teléfono y gestión de sesiones remotas;
5. catálogo de plugins y permisos por plugin;
6. paneles del HUD y estados visuales adicionales;
7. pruebas de integración para audio, GUI, dashboard y acciones del sistema.

## Decisiones de reconstrucción

- `MARK LVII` permanece como copia de referencia.
- `T.E.E.M.O.` es la copia de trabajo para la nueva versión.
- No se copian secretos ni endpoints autenticados de Torvis.
- Las funciones con efectos externos (mensajes, archivos, procesos, red,
  cámara, micrófono y energía) deben conservar confirmación explícita,
  límites de tiempo y resultado verificable.
- Cada capacidad nueva debe tener una implementación local, una prueba y una
  explicación de sus permisos.

## Limitación actual

La extracción PyInstaller no aplica porque el ejecutable observado fue
compilado con Nuitka. Las conclusiones sobre flujos internos que no aparecen
en símbolos, recursos o comportamiento observable se marcarán como inferidas,
no como código recuperado.

## Estado de esta primera versión

Implementado en T.E.E.M.O.:

- copia de trabajo independiente de MARK LVII;
- nombre visible `T.E.E.M.O.` en configuración, ventana, HUD, prompt, tareas y
  dashboard;
- dashboard conservando sus claves internas de sesión para no invalidar el
  protocolo existente;
- inventario estático de módulos y capacidades de Torvis, sin transferir
  secretos del producto instalado.

Pendiente para una equivalencia funcional más amplia:

- validar en ejecución los flujos de audio, cámara, visión y dashboard;
- añadir, uno por uno, los módulos de Torvis que no tienen equivalente en MARK
  LVII, con permisos y pruebas locales;
- decidir explícitamente si se incorporan proveedores comerciales, control
  remoto o servicios cloud. No se activan automáticamente.

La equivalencia completa no puede afirmarse solo a partir del binario: Torvis
fue compilado y no expone el código fuente original. Las observaciones directas,
las derivaciones y los elementos desconocidos deben mantenerse separados durante
la implementación.

## Observación dinámica autorizada

El 13-11-2026 se ejecutó Torvis.exe localmente durante una prueba controlada,
sin introducir credenciales ni activar funciones de micrófono, cámara o control
del sistema. El proceso principal respondió y mostró la ventana `T.O.R.V.I.S`.
El log local registró Windows 11, Python compilado 3.12.7, versión de producto
`0.6.3` y una comprobación de actualización diferida; la sesión de prueba no
mostró inicio de sesión. La aplicación mantuvo su proceso GUI activo hasta que
finalizó la ventana de observación.

Esta prueba confirma el arranque y la identidad de versión, pero no demuestra
el comportamiento de conversación, visión, audio, plugins o control remoto.
Esos flujos requieren pruebas separadas, con cada permiso y efecto externo
registrado antes de incorporarlos a T.E.E.M.O.

