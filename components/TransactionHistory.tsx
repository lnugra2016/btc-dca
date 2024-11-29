import React from 'react'
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"

interface Transaction {
  type: 'buy' | 'sell'
  amount: number
  price: number
  date: string
}

interface TransactionHistoryProps {
  transactions: Transaction[]
  averageCost: number
}

export function TransactionHistory({ transactions, averageCost }: TransactionHistoryProps) {
  return (
    <Card className="mt-4">
      <CardHeader>
        <CardTitle>Transaction History</CardTitle>
      </CardHeader>
      <CardContent>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Type</TableHead>
              <TableHead>Amount (BTC)</TableHead>
              <TableHead>Price (USD)</TableHead>
              <TableHead>Total Value (USD)</TableHead>
              <TableHead>Date</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {transactions.map((transaction, index) => {
              const totalValue = transaction.amount * transaction.price
              return (
                <TableRow key={index}>
                  <TableCell className={transaction.type === 'buy' ? 'text-green-600' : 'text-red-600'}>
                    {transaction.type.charAt(0).toUpperCase() + transaction.type.slice(1)}
                  </TableCell>
                  <TableCell>{transaction.amount.toFixed(8)}</TableCell>
                  <TableCell>${transaction.price.toFixed(2)}</TableCell>
                  <TableCell>${totalValue.toFixed(2)}</TableCell>
                  <TableCell>{new Date(transaction.date).toLocaleString()}</TableCell>
                </TableRow>
              )
            })}
          </TableBody>
        </Table>
        <div className="mt-4 text-right">
          <p className="font-bold">Costo Promedio: ${averageCost.toFixed(2)}</p>
        </div>
      </CardContent>
    </Card>
  )
}

