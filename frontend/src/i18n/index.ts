import { en } from './en'
import { zhTW } from './zh-TW'

export type Language = 'en' | 'zh-TW'
export type { Strings } from './en'

export const strings = { en, 'zh-TW': zhTW }

export function t(lang: Language) {
  return strings[lang] ?? en
}
