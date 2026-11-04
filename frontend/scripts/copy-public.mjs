// 把 public/ 下的文件复制到构建目录（sw.js 必须在站点根路径才能控制全站）。
import { createHash } from 'node:crypto'
import { copyFileSync, mkdirSync, readdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = dirname(dirname(fileURLToPath(import.meta.url)))
const source = join(root, 'public')
const target = join(root, '..', 'backend', 'static')
mkdirSync(target, { recursive: true })
for (const name of readdirSync(source)) {
  copyFileSync(join(source, name), join(target, name))
}

// 把构建产物清单写进 Service Worker，产物变化时版本号随之变化
const assets = readdirSync(join(target, 'assets')).sort().map((name) => `/assets/${name}`)
const version = createHash('sha256').update(assets.join()).digest('hex').slice(0, 12)
const worker = readFileSync(join(target, 'sw.js'), 'utf8')
writeFileSync(join(target, 'sw.js'), worker.replace('__BUILD_VERSION__', version).replace('__ASSET_LIST__', assets.join()))
