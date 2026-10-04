import { createRouter, createWebHistory } from 'vue-router'
import Home from './views/Home.vue'

const routes = [
  { path: '/', name: 'home', component: Home },
  { path: '/users/:id', name: 'user', component: () => import('./views/User.vue') },
  {
    path: '/settings',
    component: () => import('./views/Nested.vue'),
    children: [
      { path: 'profile', name: 'settings-profile', component: () => import('./views/Nested.vue') },
    ],
  },
]

export const router = createRouter({
  history: createWebHistory(),
  routes,
})
