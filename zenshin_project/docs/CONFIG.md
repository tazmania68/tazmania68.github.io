# Guía de Configuración — Ninja Zenshin Tracker

## Configuración desde la interfaz web

Toda la configuración se hace desde la propia interfaz web del tracker. Abre `http://localhost:8000` y ve a las pestañas **🔧 Conexión** y **⚙️ Ajustes**.

---

## 🔧 Pestaña Conexión

### Discord Webhook (alertas a tu celular)
1. En Discord: **Configuración del canal → Integraciones → Webhooks → Nuevo webhook**
2. Copia la URL del webhook (empieza con `https://discord.com/api/webhooks/...`)
3. Pégala en el campo **Discord Webhook** del tracker
4. Define **Mínimo de puntos para alertar** (por defecto 100) — solo se avisará de cambios mayores a este valor
5. Activa/desactiva tipos de alerta:
   - `rank`: Cambios de puesto en el ranking
   - `atk`: Ataques detectados
   - `hit`: Tu clan recibe un ataque
   - `mon`: Clanes vigilados
   - `move`: Entradas/salidas de miembros
   - `summ`: Resúmenes periódicos
   - `daily`: Resumen diario
   - `any`: Cualquier cambio (spam — usar con cuidado)

### Bot de Telegram
1. Habla con [@BotFather](https://t.me/BotFather) → `/newbot` → sigue los pasos
2. Copia el **token** que te da BotFather (formato: `123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11`)
3. Escribe `/start` a tu bot (o añádelo a un grupo y escribe algo)
4. Para saber tu **chat ID**: habla con [@userinfobot](https://t.me/userinfobot) y te dirá tu ID
5. Pega token y chat ID en el tracker
6. **Comandos del bot**:
   - `/status` — Estado del tracker y top 5
   - `/top` — Top 10 clanes por reputación
   - `/bleed` — Clanes sangrando ahora
   - `/online` — Jugadores conectados
   - `/attacks` — Últimos ataques
   - `/predict` — Predicciones de ranking
   - `/card` — Generar imagen del ranking
   - `/help` — Ayuda

### URL de miembros (auto-detectado)
El tracker intenta auto-detectar el endpoint de miembros del sitio. Si no funciona:
1. Abre el juego en tu navegador, entra a un clan
2. Abre las herramientas de desarrollador (F12) → pestaña Red (Network)
3. Busca la petición que carga los miembros (normalmente contiene "clan" o "member" en la URL)
4. Copia la URL y pégala en el campo, usando `{id}` o `{name}` como comodín
   - Ejemplo: `https://ninjazenshin.online/api/clan/{id}/members`

---

## ⚙️ Pestaña Ajustes

### Temas ninja (12 disponibles)
| Tema | Color principal | Vibra |
|---|---|---|
| Kurama | Naranja 🔥 | Modo Bestia con Cola |
| Sharingan | Rojo 👁️ | Uchiha |
| Sage | Verde 🐸 | Modo Ermitaño |
| Rinnegan | Morado 🌀 | Ojos del Sabio |
| Uchiha | Oscuro | Clan Uchiha |
| Akatsuki | Rojo/Nubes | Organización Akatsuki |
| Pain | Naranja/Morado | Los Seis Caminos |
| Orochimaru | Verde oscuro | Serpiente |
| Minato | Amarillo | Relámpago Amarillo |
| Tsunade | Verde/Rosa | La Quinta Hokage |
| Rock Lee | Verde/Naranja | La Juventud |
| Sakura | Rosa | Fuerza Médica |
| Gaara | Arena/Rojo | La Arena |

### Sonido y notificaciones
- `on`: Activar/desactivar sonidos
- `voice`: Alertas con voz (síntesis de voz del navegador)
- `vibrate`: Vibración en celulares compatibles
- `notify`: Notificaciones del navegador (pide permiso)
- `vol`: Volumen (0.0 - 1.0)
- `min_rep`: Mínima reputación para reproducir sonido
- `ticker`: Mostrar barra de ticker en vivo

### Clanes vigilados (Watchlist)
Añade clanes o jugadores a tu lista de vigilancia — recibirás alertas prioritarias y sonido especial cuando tengan actividad.

### Clanes rivales
Marca clanes como rivales — sus ataques a tu clan tendrán alertas destacadas.

### Velocidad de lectura (Polling)
- `0.5s`: Turbo — máximo detalle, más consumo de red
- `1s`: Por defecto — balanceado
- `2s`, `5s`, `15s`, `30s`, `60s`: Ahorro de recursos

El **polling adaptativo** ajusta automáticamente la velocidad: más lento cuando no hay actividad, más rápido cuando hay ataques.

---

## 📂 Archivos de configuración

Si prefieres editar a mano (avanzado), estos archivos guardan tu configuración:

| Archivo | Se edita solo? | Contenido |
|---|---|---|
| `zenshin_extras.json` | Sí | Recompensas, tokens, revendedores, capturas del Kyubi |
| `zenshin_history.json` | No (automático) | Estado, eventos, historial |
| `zenshin_series.json.gz` | No (automático) | Serie temporal de reputación |

> **Nunca edites** `zenshin_history.json` o `zenshin_series.json.gz` a mano — puedes corromper tu historial. Usa la función Backup/Restore de la interfaz.

---

## 🔒 Seguridad

- **Nunca compartas** tu token de Telegram o webhook de Discord públicamente
- El `.gitignore` ya excluye archivos de configuración local
- Si publicas tu fork, asegúrate de no subir `zenshin_extras.json` con datos sensibles
- El tracker solo se conecta a `ninjazenshin.online`, Discord y Telegram — ningún otro servidor
