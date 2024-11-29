'use client'

import React, { useEffect, useState, useMemo } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Line, LineChart, ResponsiveContainer, XAxis, YAxis } from "recharts"
import { ChartContainer, ChartTooltip, ChartTooltipContent } from "@/components/ui/chart"
import { Twitter } from 'lucide-react'
import { Button } from "@/components/ui/button"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"

interface Transaction {
  type: 'buy' | 'sell'
  amount: number
  price: number
  date: string
}

interface BitcoinPriceChartProps {
  data: { date: string; price: number }[]
  transactions: Transaction[]
  currentPrice: number
}

export function BitcoinPriceChart({ data, transactions, currentPrice }: BitcoinPriceChartProps) {
  const [interval, setInterval] = useState<'daily' | 'weekly'>('daily')
  const buyTransactions = transactions.filter(t => t.type === 'buy')

  const weeklyData = useMemo(() => {
    const weeklyPrices: { [key: string]: number[] } = {}
    data.forEach(item => {
      const date = new Date(item.date)
      const weekStart = new Date(date.setDate(date.getDate() - date.getDay()))
      const weekKey = weekStart.toISOString().split('T')[0]
      if (!weeklyPrices[weekKey]) {
        weeklyPrices[weekKey] = []
      }
      weeklyPrices[weekKey].push(item.price)
    })
    return Object.entries(weeklyPrices).map(([date, prices]) => ({
      date,
      price: prices[prices.length - 1] // Use the last price of the week
    }))
  }, [data])

  const chartData = useMemo(() => {
    const baseData = interval === 'daily' ? data : weeklyData
    return baseData.map(item => ({
      ...item,
      transactions: buyTransactions.filter(t => 
        new Date(t.date).toDateString() === new Date(item.date).toDateString()
      ).length
    }))
  }, [interval, data, weeklyData, buyTransactions])

  const customDot = (props: any) => {
    const { cx, cy, payload } = props
    if (payload.transactions > 0) {
      return <circle cx={cx} cy={cy} r={4} fill="green" />
    }
    return null
  }

  const [formattedPrice, setFormattedPrice] = useState("")

  useEffect(() => {
    const updatePrice = () => {
      if (currentPrice && !isNaN(currentPrice)) {
        setFormattedPrice(new Intl.NumberFormat('en-US', { 
          style: 'currency', 
          currency: 'USD',
          maximumFractionDigits: 2
        }).format(currentPrice))
      } else {
        setFormattedPrice("Loading...")
      }
    }
    
    updatePrice()
    const interval = setInterval(updatePrice, 1000)
    return () => clearInterval(interval)
  }, [currentPrice])

  if (!data || data.length === 0) {
    return <p className="text-white">No hay datos disponibles para el gráfico.</p>
  }

  return (
    <Card className="mt-4 relative">
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle>Precio de Bitcoin (Tiempo Real)</CardTitle>
        <div className="flex items-center gap-4">
          <Button
            variant="outline"
            size="icon"
            className="h-8 w-8"
            onClick={() => window.open('https://x.com/_luisandres', '_blank')}
          >
            <Twitter className="h-4 w-4" />
          </Button>
          <Select value={interval} onValueChange={(value: 'daily' | 'weekly') => setInterval(value)}>
            <SelectTrigger className="w-[120px]">
              <SelectValue placeholder="Select interval" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="daily">Daily</SelectItem>
              <SelectItem value="weekly">Weekly</SelectItem>
            </SelectContent>
          </Select>
          <div className="bg-black/60 text-white px-3 py-1.5 rounded">
            {formattedPrice}
          </div>
        </div>
      </CardHeader>
      <CardContent>
        <ChartContainer
          config={{
            price: {
              label: "Precio",
              color: "hsl(var(--chart-1))",
            },
          }}
          className="h-[400px]"
        >
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={chartData}>
              <XAxis
                dataKey="date"
                tickFormatter={(value) => new Date(value).toLocaleDateString()}
                interval="preserveStartEnd"
              />
              <YAxis
                tickFormatter={(value) => {
                  if (isNaN(value)) return ""
                  return `$${value.toLocaleString()}`
                }}
                domain={['dataMin', 'dataMax']}
              />
              <ChartTooltip 
                content={({ active, payload }) => {
                  if (active && payload && payload.length) {
                    const value = payload[0].value
                    if (isNaN(value)) return null
                    return (
                      <div className="bg-background/80 p-2 rounded shadow backdrop-blur-sm">
                        <p className="text-sm font-medium">
                          ${value.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}
                        </p>
                        <p className="text-xs text-muted-foreground">
                          {new Date(payload[0].payload.date).toLocaleDateString()}
                        </p>
                        {payload[0].payload.transactions > 0 && (
                          <p className="text-xs text-green-500">
                            Transacciones: {payload[0].payload.transactions}
                          </p>
                        )}
                      </div>
                    )
                  }
                  return null
                }}
              />
              <Line
                type="monotone"
                dataKey="price"
                stroke="var(--color-price)"
                strokeWidth={2}
                dot={customDot}
              />
            </LineChart>
          </ResponsiveContainer>
        </ChartContainer>
      </CardContent>
    </Card>
  )
}

