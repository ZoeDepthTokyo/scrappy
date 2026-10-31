# Scrappy

Scrappy is a standalone Windows app that searches Mexico City rental listings, scores them, and helps you review candidates on a map and in a Kanban board. It runs a local server on your computer; listing records, feedback, notes, and settings are stored locally.

## Download and install

The source repository is https://github.com/ZoeDepthTokyo/scrappy.

Download `Scrappy.exe` and `SHA256SUMS.txt` from [GitHub Releases](https://github.com/ZoeDepthTokyo/scrappy/releases). In PowerShell, run `Get-FileHash .\Scrappy.exe -Algorithm SHA256` and compare the hash with the `Scrappy.exe` line in `SHA256SUMS.txt`. Start the executable after verifying it. This is an unsigned beta, so Windows SmartScreen may appear; choose **More info → Run anyway** only if you trust the verified download.

## First run

Use the setup panel to choose AI or No AI, optionally configure Firecrawl, and adjust Search criteria. You can revisit these choices in Settings.

For a guided walkthrough and feedback form, see [BETA_TESTING.md](BETA_TESTING.md).

## Connect AI

In Settings → AI, sign in with OpenRouter OAuth without copying a key, or select Gemini, OpenAI, Anthropic, or Ollama Cloud and enter a provider key in Settings. Choose No AI to use metadata scoring. **Your AI agent can do this for you: see [AGENTS.md](AGENTS.md) and [prompts/](prompts/).** Never paste credentials into chat. See [PRIVACY.md](PRIVACY.md).

## Firecrawl

Firecrawl is optional and can provide access to some sources and fallback results. It may incur charges. Read [FIRECRAWL.md](FIRECRAWL.md) before enabling it.

## Daily use

Review the cockpit at `/favorites` and organize listings in `/kanban`. Set your search criteria and select **Run Now**. Listing availability depends on the sources and current results.

## Backup, update, and uninstall

Use Settings → Export to create a JSON backup and Settings → Import to restore it. Backups include personal listing information and notes; store them securely. Credentials are stored separately in the operating system credential store. To update, quit Scrappy and replace the executable; local data remains. To uninstall, quit and remove the executable. The data and saved credentials remain unless you separately choose to remove them.

## Customize it

Run from source: `python -m venv .venv`, then `.venv\Scripts\python.exe -m pip install -e .` and `.venv\Scripts\python.exe launcher.py` (it keeps running in that window until you choose Quit; use a second window for other commands). Fork the project and create a branch to customize it. See [DEVELOPMENT.md](DEVELOPMENT.md).

## Privacy

Listing sites receive requests during a search. The selected AI provider can receive photo URLs for scoring; Firecrawl receives requested listing-page URLs if enabled; map tiles load from OpenStreetMap. See [PRIVACY.md](PRIVACY.md).

## License

MIT. See [LICENSE](LICENSE).

---

# Español

Scrappy es una aplicación independiente para Windows que busca anuncios de renta en Ciudad de México, los puntúa y permite revisarlos en un mapa y un tablero Kanban. Ejecuta un servidor local en tu computadora; los anuncios, comentarios, notas y ajustes se guardan localmente.

## Descarga e instalación

Descarga `Scrappy.exe` y `SHA256SUMS.txt` desde [GitHub Releases](https://github.com/ZoeDepthTokyo/scrappy/releases). En PowerShell ejecuta `Get-FileHash .\Scrappy.exe -Algorithm SHA256` y compara el hash con la línea `Scrappy.exe` de `SHA256SUMS.txt`. Inicia el ejecutable después de verificarlo. Esta es una beta sin firma, así que Windows SmartScreen puede aparecer; selecciona **Más información → Ejecutar de todas formas** solo si confías en la descarga verificada.

## Primer inicio

En el panel de configuración elige IA o No AI, configura Firecrawl si quieres y ajusta los criterios de búsqueda. Puedes cambiar estas opciones después en Configuración.

Para una guía paso a paso y un formulario de comentarios, consulta [BETA_TESTING.md](BETA_TESTING.md).

## Conectar IA

En Configuración → IA, inicia sesión con OpenRouter OAuth sin copiar una clave, o selecciona Gemini, OpenAI, Anthropic u Ollama Cloud e introduce la clave del proveedor en Configuración. Elige No AI para usar puntuación basada en metadatos. **Tu agente de IA puede ayudarte: consulta [AGENTS.md](AGENTS.md) y [prompts/](prompts/).** Nunca pegues credenciales en el chat. Consulta [PRIVACY.md](PRIVACY.md).

## Firecrawl

Firecrawl es opcional y puede dar acceso a algunas fuentes y resultados alternativos. Puede generar cargos. Lee [FIRECRAWL.md](FIRECRAWL.md) antes de activarlo.

## Uso diario

Revisa el cockpit en `/favorites` y organiza anuncios en `/kanban`. Ajusta los criterios y selecciona **Run Now**. La disponibilidad depende de las fuentes y los resultados actuales.

## Respaldo, actualización y desinstalación

Usa Configuración → Exportar para crear un respaldo JSON y Configuración → Importar para restaurarlo. Los respaldos contienen información personal de anuncios y notas; guárdalos de forma segura. Las credenciales se guardan aparte en el almacén de credenciales del sistema operativo. Para actualizar, cierra Scrappy y reemplaza el ejecutable; los datos locales permanecen. Para desinstalar, cierra la aplicación y elimina el ejecutable. Los datos y las credenciales guardadas permanecen hasta que decidas eliminarlos por separado.

## Personalizar

Ejecuta desde el código: `python -m venv .venv`, luego `.venv\Scripts\python.exe -m pip install -e .` y `.venv\Scripts\python.exe launcher.py` (se queda abierto en esa ventana hasta que elijas Salir; usa otra ventana para otros comandos). Crea un fork y una rama para personalizarlo. Consulta [DEVELOPMENT.md](DEVELOPMENT.md).

## Privacidad

Los sitios de anuncios reciben solicitudes durante una búsqueda. El proveedor de IA seleccionado puede recibir URL de fotos para puntuarlas; Firecrawl recibe URL de páginas de anuncios si está activado; los mosaicos del mapa se cargan desde OpenStreetMap. Consulta [PRIVACY.md](PRIVACY.md).

## Licencia

MIT. Consulta [LICENSE](LICENSE).
