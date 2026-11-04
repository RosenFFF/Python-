import { createRouter, createWebHistory } from 'vue-router'
import { can, homePath, state } from './store'

const v = (name) => () => import(`./views/${name}.vue`)

// perm：满足其一可进入。接口层还会再校验一次，这里只决定导航。
const routes = [
  { path: '/login', component: v('Login'), meta: { public: true, bare: true } },
  { path: '/', redirect: () => homePath() },
  { path: '/family', component: v('FamilyHome'), meta: { perm: ['review:create'], title: '首页' } },
  { path: '/family/book', component: v('FamilyBook'), meta: { perm: ['order:create'], title: '预约服务' } },
  { path: '/family/health', component: v('FamilyHealth'), meta: { perm: ['health:read'], title: '健康记录' } },
  { path: '/family/bills', component: v('FamilyBills'), meta: { perm: ['statement:read'], title: '我的账单' } },
  { path: '/cg', component: v('CaregiverToday'), meta: { perm: ['exec:perform'], title: '今日工单' } },
  { path: '/cg/order/:id', component: v('CaregiverExecute'), meta: { perm: ['exec:perform'], title: '上门服务' } },
  { path: '/work', component: v('Workbench'), meta: { perm: ['dispatch:manage'], title: '工作台' } },
  { path: '/orders', component: v('Orders'), meta: { perm: ['order:read'], title: '工单管理' } },
  { path: '/orders/:id', component: v('OrderDetail'), meta: { perm: ['order:read'], title: '工单详情' } },
  { path: '/dispatch/:id', component: v('DispatchDesk'), meta: { perm: ['dispatch:manage'], title: '智能派单' } },
  { path: '/exceptions', component: v('Exceptions'), meta: { perm: ['exception:handle'], title: '异常处置' } },
  { path: '/alerts', component: v('Alerts'), meta: { perm: ['alert:read'], title: '预警看板' } },
  { path: '/elders', component: v('Elders'), meta: { perm: ['elder:read'], title: '老人档案' } },
  { path: '/elders/:id', component: v('ElderDetail'), meta: { perm: ['elder:read'], title: '档案详情' } },
  { path: '/quality', component: v('Quality'), meta: { perm: ['complaint:read', 'followup:handle'], title: '回访与投诉' } },
  { path: '/bills', component: v('Bills'), meta: { perm: ['billing:read'], title: '月度账单' } },
  { path: '/bills/:id', component: v('BillDetail'), meta: { perm: ['billing:read'], title: '账单详情' } },
  { path: '/statements', component: v('Statements'), meta: { perm: ['billing:payment'], title: '缴费登记' } },
  { path: '/performance', component: v('Performance'), meta: { perm: ['perf:read'], title: '绩效榜' } },
  { path: '/stats/:kind', component: v('Dashboard'), meta: { perm: ['stats:community', 'stats:org', 'stats:street'], title: '统计看板' } },
  { path: '/catalog', component: v('Catalog'), meta: { perm: ['catalog:read'], title: '服务目录' } },
  { path: '/caregivers', component: v('Caregivers'), meta: { perm: ['caregiver:write'], title: '护理员管理' } },
  { path: '/rules', component: v('Rules'), meta: { perm: ['rule:manage'], title: '预警规则' } },
  { path: '/audit', component: v('Audit'), meta: { perm: ['audit:read'], title: '审计日志' } },
  { path: '/admin/users', component: v('AdminUsers'), meta: { perm: ['user:manage'], title: '用户管理' } },
  { path: '/admin/roles', component: v('AdminRoles'), meta: { perm: ['role:manage'], title: '角色权限' } },
  { path: '/admin/configs', component: v('AdminConfigs'), meta: { perm: ['config:manage'], title: '业务参数' } },
  { path: '/admin/backups', component: v('AdminBackups'), meta: { perm: ['backup:manage'], title: '备份恢复' } },
  { path: '/admin/jobs', component: v('AdminJobs'), meta: { perm: ['job:manage'], title: '定时任务' } },
  { path: '/:pathMatch(.*)*', redirect: '/' },
]

export const router = createRouter({ history: createWebHistory(), routes })

router.beforeEach((to) => {
  if (to.meta.public) return true
  if (!state.user) return '/login'
  if (to.meta.perm && !can(...to.meta.perm)) return homePath()
  return true
})

router.afterEach((to) => {
  document.title = to.meta.title ? `${to.meta.title} · 颐护通` : '颐护通'
})
