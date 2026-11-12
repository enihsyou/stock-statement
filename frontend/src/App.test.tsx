// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import App from './App'
import { api } from './api'

beforeEach(() => {
  localStorage.clear()
  vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() })))
})
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks() })

async function chooseAndUpload() {
  const file = new File(['anonymous statement'], 'anonymous.txt', {type: 'text/plain'})
  Object.defineProperty(file, 'arrayBuffer', {value: async () => new TextEncoder().encode('anonymous statement').buffer})
  fireEvent.change(screen.getByLabelText('选择交割单'), {target: {files: [file]}})
  fireEvent.click(screen.getByRole('button', {name: '分析 1 个文件'}))
}

describe('App upload and report', () => {
  it('shows server upload errors and keeps files available for retry', async () => {
    const fetcher = vi.fn().mockResolvedValue({ok: false, status: 422, json: async () => ({detail: '匿名文件缺少必需表头'})})
    vi.stubGlobal('fetch', fetcher)
    render(<App />)
    await chooseAndUpload()
    expect(await screen.findByRole('alert')).toHaveProperty('textContent', '匿名文件缺少必需表头')
    expect(screen.getByRole('button', {name: '分析 1 个文件'})).toHaveProperty('disabled', false)
    expect(fetcher).toHaveBeenCalledTimes(1)
    expect(Object.keys(localStorage)).toEqual(['theme'])
    fireEvent.click(screen.getByRole('button', {name: '移除'}))
    expect(screen.queryByRole('alert')).toBeNull()
    fireEvent.change(screen.getByLabelText('选择交割单'), {target: {files: [new File(['replacement'], 'replacement.txt')]}})
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('clears a previous upload error when another file is selected', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ok:false,status:422,json:async()=>({detail:'旧文件错误'})}))
    render(<App />)
    await chooseAndUpload()
    await screen.findByRole('alert')
    fireEvent.change(screen.getByLabelText('选择交割单'), {target:{files:[new File(['replacement'], 'replacement.txt')]}})
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('renders the exact backend money strings and clears the server session', async () => {
    const session = {id: 'token', accounts: ['匿名账户'], securities: []}
    const report = {metadata: {start_date:'20260101',end_date:'20260102',files_count:1,entries_count:2,duplicates:0,notes:[]}, incomplete:false, columns:[{key:'name',title:'证券名称',numeric:false,change:false},{key:'profit',title:'已实现收益',numeric:true,change:true}], rows:[{name:'匿名证券',profit:'12345678901234567.89'}], totals:{name:'合计',profit:'-0.01'}, overview:[],unknown:[]}
    const fetcher = vi.fn().mockResolvedValueOnce({ok:true,status:200,json:async()=>session}).mockResolvedValueOnce({ok:true,status:200,json:async()=>report}).mockResolvedValueOnce({ok:true,status:204})
    vi.stubGlobal('fetch', fetcher)
    render(<App />)
    await chooseAndUpload()
    expect(await screen.findByText('12345678901234567.89')).toBeTruthy()
    expect(screen.getByText('-0.01')).toBeTruthy()
    expect(Object.keys(localStorage)).toEqual(['theme'])
    fireEvent.click(screen.getByRole('button', {name:'清空本次会话'}))
    await screen.findByText('拖放交割单到这里')
    expect(fetcher.mock.calls[2][0]).toBe('/api/sessions/token')
    expect(fetcher.mock.calls[2][1].method).toBe('DELETE')
  })

  it('persists only the selected theme preference', async () => {
    render(<App />)
    fireEvent.change(screen.getByLabelText('主题'), {target:{value:'dark'}})
    await waitFor(() => expect(document.documentElement.classList.contains('dark')).toBe(true))
    expect({...localStorage}).toEqual({theme:'dark'})
  })
})

it('forwards AbortSignal and disables HTTP caching', async () => {
  const controller = new AbortController()
  const fetcher = vi.fn().mockResolvedValue({ok:true,status:200,json:async()=>({})})
  vi.stubGlobal('fetch', fetcher)
  await api('/api/sessions/test/report', {signal:controller.signal})
  expect(fetcher.mock.calls[0][1].signal).toBe(controller.signal)
  expect(fetcher.mock.calls[0][1].cache).toBe('no-store')
})
