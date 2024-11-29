import React, { useState, useCallback } from 'react'
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"

interface TransactionFormProps {
  onAddTransaction: (transaction: { type: 'buy' | 'sell'; amount: number; price: number; date: string }) => void
  currentPrice: number
  onSaveTransaction: () => void
  onClearTransactions: () => void
}

export function TransactionForm({ onAddTransaction, currentPrice, onSaveTransaction, onClearTransactions }: TransactionFormProps) {
  const [type, setType] = useState<'buy' | 'sell'>('buy')
  const [amount, setAmount] = useState('')

  const handleSubmit = useCallback((e: React.FormEvent) => {
    e.preventDefault()
    onAddTransaction({
      type,
      amount: parseFloat(amount),
      price: currentPrice,
      date: new Date().toISOString(),
    })
    setAmount('')
  }, [type, amount, currentPrice, onAddTransaction])

  return (
    <form onSubmit={handleSubmit} className="mb-4">
      <div className="flex space-x-2">
        <Select onValueChange={(value) => setType(value as 'buy' | 'sell')}>
          <SelectTrigger className="w-[180px]">
            <SelectValue placeholder="Select transaction type" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="buy">Buy</SelectItem>
            <SelectItem value="sell">Sell</SelectItem>
          </SelectContent>
        </Select>
        <Input
          type="number"
          value={amount}
          onChange={(e) => setAmount(e.target.value)}
          placeholder="Amount of Bitcoin"
          step="0.00000001"
          min="0"
          required
        />
        <Button type="submit">Add Transaction</Button>
        <Button type="button" onClick={onSaveTransaction} variant="outline">Save Transactions</Button>
        <Button type="button" onClick={onClearTransactions} variant="destructive">Clear All</Button>
      </div>
    </form>
  )
}

