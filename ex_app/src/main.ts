import { createApp } from 'vue'
import App from './App.vue'
import '@nextcloud/dialogs/style.css'
const target = document.querySelector('#content')
if (target) createApp(App).mount(target)
