# Changelog — Ninja Zenshin Tracker

Todas las cambios notables del proyecto se documentan en este archivo.

El formato se basa en [Keep a Changelog](https://keepachangelog.com/es-ES/1.0.0/).

## [v23] — 2026 — Sabio de los Seis Caminos

### Añadido
- **Bleed tracking**: Detección de clanes "sangrando" (perdiendo REP continuamente) con alertas dedicadas
- **Bot de Telegram interactivo**: Comandos `/status`, `/top`, `/bleed`, `/online`, `/attacks`, `/predict`, `/card`, `/help`
- **Generador de cartas PNG**: Imágenes del ranking para compartir (requiere Pillow)
- **Embeds ricos en Discord**: Mensajes con color, campos y formato enriquecido
- **Modo online**: Jugadores conectados en la última hora agrupados por clan
- **Rachas de ataque (streaks)**: Detecta cuando un clan ataca repetidamente en racha
- **8 temas nuevos**: Akatsuki, Pain, Orochimaru, Minato, Tsunade, Rock Lee, Sakura, Gaara
- **Cache de conexión persistente (Keep-Alive)**: Reduce la latencia reutilizando conexiones TLS
- **Polling adaptativo**: Ajusta automáticamente la velocidad de lectura según la actividad
- **Puntaje de amenaza**: Evalúa qué clanes son más peligrosos
- **Comparador de clanes**: Compara lado a lado dos clanes
- **Widget de predicciones**: Proyección del ranking final de temporada
- **Filtro de eventos**: Filtra la línea de tiempo por tipo/clan/jugador
- **Ticker en vivo**: Barra inferior con actividad reciente
- **Modo compacto**: Interfaz más densa para ver más datos
- **Instalación PWA**: Instala el tracker como app en tu celular
- **Atajos de teclado**: Navegación rápida por pestañas (1-9, Ctrl+F, etc.)
- **Backup/Restore**: Exporta e importa todo tu historial y configuración
- **Exportar CSV**: Exporta datos de clanes, miembros y eventos
- **Multi-idioma (i18n)**: Traducción automática de la interfaz
- **Recompensas y tokens**: Panel con precios oficiales de tokens y revendedores verificados
- **Archivo de temporada**: Detecta automáticamente la temporada y ronda actual
- **Top Burn**: Quién específico de cada clan está ganando reputación (atacando)

### Corregido
- Fix en `update_streaks`: comparación de timestamp incorrecta (`e.get("t")` → `e.get("ts")`)
- Mejorada la detección de IDs de clan desde atributos HTML
- Fix en la descompresión gzip de respuestas
- Mayor robustez en el parser de tablas HTML

## [v22] — Versión anterior

### Añadido
- Alertas por sonido, voz, vibración y notificaciones del navegador
- Integración con Telegram (alertas básicas)
- Temas ninja: Kurama, Sharingan, Sage, Rinnegan, Uchiha
- Predicciones de ranking final
- Logros desbloqueables
- Ticker de actividad en vivo

## [v16-v21] — Versiones históricas

- Tracker base con scraping de ranking de clanes
- Detección de ataques por emparejamiento de ganancias/pérdidas
- Alertas por Discord webhook
- Interfaz web con Server-Sent Events (SSE)
- Historial de reputación comprimido (gzip)
- Lectura paralela de miembros de clanes
- PvE y PvP tracking en vivo
- Mapa de calor de actividad (heatmap)
