# 🥷 Ninja Zenshin Tracker v23 — Sabio de los Seis Caminos

> Tracker en vivo para **Ninja Zenshin Online** — clanes, reputación, ataques, PvP, PvE, alertas por Discord/Telegram, predicciones de ranking y mucho más. Todo en un solo archivo Python, sin dependencias obligatorias.

![Version](https://img.shields.io/badge/version-v23-orange) ![Python](https://img.shields.io/badge/python-3.8%2B-blue) ![License](https://img.shields.io/badge/license-MIT-green) ![No dependencies](https://img.shields.io/badge/dependencies-only%20stdlib%20%2B%20optional%20Pillow-red)

---

## ✨ Características principales

| Funcionalidad | Descripción |
|---|---|
| **📊 Tracking en vivo** | Lee el ranking de clanes cada segundo (modo turbo 0.5s) y detecta ganancias/pérdidas de reputación al instante |
| **⚔️ Detección de ataques** | Identifica quién atacó a quién emparejando ganancias y pérdidas exactas de REP |
| **🔥 Bleed / Sangrado** | Detecta cuando un clan está perdiendo reputación de forma continua |
| **👥 Miembros en vivo** | Lee los miembros de cada clan y detecta qué jugador específico atacó (Top Burn) |
| **🔔 Alertas** | Notificaciones por **Discord** (webhook) y **Telegram** (bot) con embeds ricos |
| **🎨 12 temas ninja** | Kurama, Sharingan, Sage, Rinnegan, Uchiha, Akatsuki, Pain, Orochimaru, Minato, Tsunade, Rock Lee, Sakura, Gaara |
| **🔮 Predicciones** | Proyecta el ranking final de temporada según el ritmo actual |
| **⚡ Puntaje de amenaza** | Evalúa qué clanes son más peligrosos según su actividad |
| **📈 Gráficas de historial** | Reputación por clan a lo largo del tiempo (26h alta resolución + 41 días) |
| **🏆 Logros** | Sistema de logros desbloqueables mientras usas el tracker |
| **💾 Backup / Restore** | Exporta e importa todo tu historial y configuración |
| **📤 Exportar CSV** | Exporta datos de clanes, miembros y eventos |
| **🌐 Multi-idioma** | Traducción automática de la interfaz (i18n) |
| **📱 PWA** | Instalable como app en celular (modo pantalla completa) |
| **⌨️ Atajos de teclado** | Navegación rápida por pestañas |
| **🖼️ Generador de cartas** | Crea imágenes PNG del ranking para compartir (requiere Pillow) |

---

## 🚀 Instalación rápida

### Requisitos
- **Python 3.8 o superior** (no necesita librerías externas para funcionar)
- *Opcional:* `Pillow` para generar imágenes/cartas del ranking

### 1. Descargar
```bash
git clone https://github.com/TU_USUARIO/zenshin-tracker.git
cd zenshin-tracker
```

O simplemente descarga `zenshin_tracker.py` — **es un solo archivo**.

### 2. Ejecutar
```bash
python3 zenshin_tracker.py
```

### 3. Abrir en el navegador
```
http://localhost:8000
```

¡Listo! El tracker empezará a leer el ranking automáticamente.

---

## ⚙️ Opciones de línea de comandos

```bash
python3 zenshin_tracker.py --port 8080        # Cambiar puerto
python3 zenshin_tracker.py --poll 0.5         # Modo turbo (lectura cada 0.5s)
python3 zenshin_tracker.py --url OTRO_URL     # URL alternativa del ranking
```

| Opción | Valor por defecto | Descripción |
|---|---|---|
| `--port` | `8000` | Puerto del servidor web |
| `--poll` | `1` | Segundos entre lecturas (0.5 = turbo, 30 = ahorro) |
| `--url` | `ninjazenshin.online` | URL del panel de ranking |

---

## 📡 Configuración de alertas (Discord / Telegram)

### Discord (Webhook)
1. Crea un webhook en tu servidor de Discord: **Configuración del canal → Integraciones → Webhooks → Nuevo webhook**
2. Copia la URL del webhook
3. Abre el tracker → pestaña **🔧 Conexión** → pega la URL en **Discord Webhook**
4. Define el mínimo de puntos para alertar (por defecto 100)

### Telegram (Bot)
1. Habla con [@BotFather](https://t.me/BotFather) en Telegram y crea un bot nuevo
2. Copia el **token** del bot
3. Escribe `/start` a tu bot (o añádelo a un grupo)
4. Abre el tracker → pestaña **🔧 Conexión** → pega el **token** y tu **chat ID**
5. Comandos disponibles: `/status`, `/top`, `/bleed`, `/online`, `/attacks`, `/predict`, `/card`, `/help`

---

## 🖥️ Interfaz web

El tracker incluye una interfaz web completa con estas pestañas:

- **🏠 Inicio** — Resumen en vivo, top clanes, ticker de actividad
- **⚔️ Ataques** — Línea de tiempo de ataques detectados
- **🔥 Bleed** — Clanes perdiendo reputación ahora
- **👥 Miembros** — Detalle por clan, Top Burn (quién atacó)
- **📈 Gráficas** — Historial de reputación por clan
- **🎯 PvE / PvP** — Rankings de jugadores en vivo
- **🔮 Predicciones** — Proyección del ranking final
- **🏆 Logros** — Logros desbloqueados
- **🔧 Conexión** — Configurar Discord, Telegram, URL de miembros
- **⚙️ Ajustes** — Temas, alertas, filtros, backup/restore

---

## 📁 Archivos generados

El tracker crea estos archivos automáticamente (no los borres si quieres conservar tu historial):

| Archivo | Contenido |
|---|---|
| `zenshin_history.json` | Historial de eventos y estado |
| `zenshin_series.json.gz` | Serie temporal de reputación por clan (comprimido) |
| `zenshin_extras.json` | Configuración de recompensas, tokens, revendedores |
| `zenshin_img/` | Imágenes subidas (capturas del Kyubi, etc.) |
| `i18n/` | Traducciones de la interfaz |

---

## 🤝 Contribuir

¡Las contribuciones son bienvenidas! Si encuentras un bug o tienes una idea:

1. Haz un **Fork** del proyecto
2. Crea una rama para tu feature (`git checkout -b feature/IncreibleFeature`)
3. Haz commit de tus cambios (`git commit -m 'Add IncreibleFeature'`)
4. Push a la rama (`git push origin feature/IncreibleFeature`)
5. Abre un **Pull Request**

---

## 📜 Licencia

Este proyecto está bajo la licencia MIT — ver el archivo [LICENSE](LICENSE) para detalles.

---

## ⚠️ Descargo de responsabilidad

Este proyecto es una herramienta de terceros no afiliada a Ninja Zenshin Online ni a sus desarrolladores. Se proporciona "tal cual" para uso personal y comunitario. Usa los datos de forma responsable y respeta los términos de servicio del juego.

---

<p align="center">
  <b>Hecho con 🔥 por la comunidad de Ninja Zenshin</b><br>
  <i>Modo Sabio de los Seis Caminos activado</i>
</p>
