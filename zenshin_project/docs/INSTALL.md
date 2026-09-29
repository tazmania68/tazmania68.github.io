# Guía de Instalación — Ninja Zenshin Tracker

## Requisitos del sistema

| Requisito | Mínimo | Recomendado |
|---|---|---|
| Python | 3.8 | 3.10+ |
| RAM | 256 MB | 512 MB |
| Disco | 50 MB | 200 MB (para historial largo) |
| Conexión a internet | Obligatoria | Estable |

Sistemas operativos compatibles: Windows, macOS, Linux, Android (Termux), Raspberry Pi.

---

## Método 1: Un solo archivo (más fácil)

El tracker es **un solo archivo Python** — no necesita instalación.

1. Descarga `zenshin_tracker.py`
2. Abre una terminal en la carpeta donde lo guardaste
3. Ejecuta:
   ```bash
   python3 zenshin_tracker.py
   ```
4. Abre tu navegador en `http://localhost:8000`

---

## Método 2: Desde GitHub

```bash
# Clonar el repositorio
git clone https://github.com/TU_USUARIO/zenshin-tracker.git
cd zenshin-tracker

# (Opcional) Crear entorno virtual
python3 -m venv venv
source venv/bin/activate   # Linux/macOS
# venv\Scripts\activate    # Windows

# (Opcional) Instalar Pillow para generar imágenes
pip install -r requirements.txt

# Ejecutar
python3 zenshin_tracker.py
```

---

## Método 3: Instalar Pillow (opcional, para imágenes)

Pillow permite generar cartas/imágenes del ranking para compartir:

```bash
pip install Pillow
```

Sin Pillow, el tracker funciona igual — solo no podrás usar el botón "Generar imagen".

---

## Verificar que funciona

1. En la terminal deberías ver:
   ```
   Tracker v23 · SABIO DE LOS SEIS CAMINOS listo -> abre http://localhost:8000
   ```
2. Abre `http://localhost:8000` en tu navegador
3. En 1-2 segundos deberías ver el ranking de clanes actualizándose en vivo
4. La pestaña "⚔️ Ataques" mostrará eventos cuando los clanes ganen/pierdan reputación

### Acceder desde el celular (misma WiFi)

Si tu PC y celular están en la misma red WiFi, la terminal mostrará una URL como:
```
En el celular (misma WiFi): http://192.168.x.x:8000
```
Abre esa URL en el navegador de tu celular.

---

## Problemas comunes

### "El puerto 8000 ya está en uso"
Usa otro puerto:
```bash
python3 zenshin_tracker.py --port 8080
```

### "No encuentro la tabla de clanes"
- Verifica tu conexión a internet
- El sitio podría estar caído — espera unos minutos
- Prueba con una URL diferente: `--url https://OTRO_URL`

### Los miembros no se cargan
- Ve a la pestaña **🔧 Conexión**
- El tracker intenta auto-detectar el endpoint de miembros
- Si no funciona, usa el "Asistente de conexión" para capturar la URL manualmente

### Python no está instalado
- **Windows**: Descarga desde [python.org](https://www.python.org/downloads/) — marca "Add to PATH"
- **macOS**: `brew install python3`
- **Linux (Ubuntu/Debian)**: `sudo apt install python3`
- **Android**: Instala [Termux](https://termux.dev/) y luego `pkg install python`

---

## Actualizar a una nueva versión

1. Descarga el nuevo `zenshin_tracker.py`
2. Reemplaza el archivo antiguo (**no borres** `zenshin_history.json` ni `zenshin_series.json.gz` — ahí está tu historial)
3. Ejecuta de nuevo — tu historial y configuración se conservan
