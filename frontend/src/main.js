import { createApp } from 'vue'
import App from './App.vue'
import { registerServiceWorker } from './offline'
import { router } from './router'
import { restoreSession, state } from './store'
import { syncClock } from './utils'
import './styles.css'

restoreSession().finally(() => {
  syncClock(state.meta?.server_time)
  createApp(App).use(router).mount('#app')
  registerServiceWorker()
})
