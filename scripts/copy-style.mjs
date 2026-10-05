import { copyFileSync, mkdirSync } from 'node:fs'
mkdirSync('ex_app/css', { recursive: true })
copyFileSync('ex_app/js/events-main.css', 'ex_app/css/events-main.css')
