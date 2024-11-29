import React from 'react'
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"

interface Transaction {
  type: 'buy' | 'sell'
  amount: number
  price: number
  date: string
}

interface PortfolioSummaryProps {
  transactions: Transaction[]
  currentPrice: number
}

export function PortfolioSummary({ transactions, currentPrice }: PortfolioSummaryProps) {
  const totalBitcoin = transactions.reduce((total, t) => total + (t.type === 'buy' ? t.amount : -t.amount), 0)
  const totalInvested = transactions.reduce((total, t) => total + (t.type === 'buy' ? t.amount * t.price : -t.amount * t.price), 0)
  const currentValue = totalBitcoin * currentPrice
  const profitLoss = currentValue - totalInvested

  return (
    <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
          <CardTitle className="text-sm font-medium">Total Bitcoin</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="text-2xl font-bold">{totalBitcoin.toFixed(8)} BTC</div>
        </CardContent>
      </Card>
      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
          <CardTitle className="text-sm font-medium">Current Value</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="text-2xl font-bold">${currentValue.toFixed(2)}</div>
        </CardContent>
      </Card>
      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
          <CardTitle className="text-sm font-medium">Total Invested</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="text-2xl font-bold">${totalInvested.toFixed(2)}</div>
        </CardContent>
      </Card>
      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
          <CardTitle className="text-sm font-medium">Profit/Loss</CardTitle>
        </CardHeader>
        <CardContent>
          <div className={`text-2xl font-bold ${profitLoss >= 0 ? 'text-green-600' : 'text-red-600'}`}>
            ${profitLoss.toFixed(2)}
          </div>
        </CardContent>
      </Card>
    </div>
  )
}

