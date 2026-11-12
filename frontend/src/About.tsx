import { Dialog, Tabs } from "radix-ui";
import { Button } from "@/components/ui/button";

export default function About() {
  return <Dialog.Root>
    <Dialog.Trigger asChild><Button variant="ghost">关于与隐私</Button></Dialog.Trigger>
    <Dialog.Portal>
      <Dialog.Overlay className="about-overlay" />
      <Dialog.Content className="about-dialog">
        <div className="about-heading"><Dialog.Title>交割单 · 交易计划</Dialog.Title><Dialog.Close asChild><Button variant="ghost" aria-label="关闭说明">×</Button></Dialog.Close></div>
        <Dialog.Description className="sr-only">核算方式、策略示例和数据使用说明</Dialog.Description>
        <Tabs.Root defaultValue="algorithm">
          <Tabs.List className="about-tabs" aria-label="说明分类"><Tabs.Trigger value="algorithm">如何核算</Tabs.Trigger><Tabs.Trigger value="privacy">隐私说明</Tabs.Trigger></Tabs.List>
          <div className="about-panels">
          <Tabs.Content value="algorithm" className="about-content" forceMount>
            <div className="calculation-flow" aria-label="交割单经过核算后生成持仓和交易计划"><div><strong>交割单</strong><small>去重 · 补全成交时间</small></div><span aria-hidden="true">→</span><div><strong>资金与持仓</strong><small>移动加权成本</small></div><span aria-hidden="true">→</span><div><strong>下一笔交易</strong><small>现价 · 费用 · 数量</small></div></div>
            <p>持仓成本包含买入费用；卖出净收款减去对应成本，得到已实现收益。总经济盈亏由净现金流与剩余持仓的现价估值构成。</p>
            <div className="strategy-examples">
              <section><h3>A 保留买入</h3><div className="example-trades"><span className="buy">买入 10 元</span><span aria-hidden="true">→</span><span className="sell">卖出 9 元</span></div><div className="example-track"><span className="example-buy">10 元买入参考保留</span><span className="example-sell">9 元以下回补参考</span></div><p>亏损卖出保留原买入参考；零持仓时不生成可操作卖出计划。</p></section>
              <section><h3>B 当前批次</h3><div className="example-trades"><span className="buy">买入 10 元</span><span aria-hidden="true">→</span><span className="sell">卖出 9 元</span></div><div className="example-track"><span className="example-closed">原持仓批次扣减</span><span className="example-sell">9 元以下回补参考</span></div><p>卖出消耗真实持仓批次；低价买回创建新批次，回补改善不重复计收益。</p></section>
              <section><h3>A 保留买入</h3><div className="example-trades"><span className="buy">买入 10 元</span><span aria-hidden="true">→</span><span className="sell">卖出 11 元</span></div><div className="example-track"><span className="example-closed">10 元买入参考完成配对</span><span className="example-buy">每股价差收益 1 元（未计费用）</span></div><p>等量盈利卖出后，原买入参考不再保留，也不生成回补参考。</p></section>
              <section><h3>B 当前批次</h3><div className="example-trades"><span className="buy">买入 10 元</span><span aria-hidden="true">→</span><span className="sell">卖出 11 元</span></div><div className="example-track"><span className="example-closed">原持仓批次全部卖出</span><span className="example-buy">每股价差收益 1 元（未计费用）</span></div><p>等量盈利卖出后，当前批次结束，不生成回补参考；再次买入创建新批次。</p></section>
            </div>
            <p className="muted">两种策略独立归因，不能相加。含费用盈利价按设置的操作量计算；计划数量以交割单为依据，可用资金和券商实时可卖数量仍需确认。</p>
          </Tabs.Content>
          <Tabs.Content value="privacy" className="about-content" forceMount>
            <dl className="privacy-facts"><div><dt>交割单</dt><dd>文件内容发送到当前运行的分析服务，在内存中处理；不写入磁盘、访问日志或浏览器存储，不连接券商账户。</dd></div><div><dt>行情</dt><dd>向行情来源请求公共行情，只发送证券标识和日期范围。历史行情和收盘后现价可缓存到服务所在电脑，不含账户或交易记录。</dd></div><div><dt>浏览器设置</dt><dd>主题、费率、最低佣金、操作手数和候选价保存在 localStorage，跨会话保留。</dd></div><div><dt>清空与退出</dt><dd>清空会话删除服务内存中的本次记录；退出服务释放所有会话。页面刷新不会恢复交易数据，没有闲置自动清空。</dd></div></dl>
          </Tabs.Content>
          </div>
        </Tabs.Root>
      </Dialog.Content>
    </Dialog.Portal>
  </Dialog.Root>;
}
