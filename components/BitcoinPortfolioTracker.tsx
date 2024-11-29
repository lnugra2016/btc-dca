'use client'

import React, { useState, useEffect, useCallback, useMemo } from 'react'
import { TransactionForm } from './TransactionForm'
import { PortfolioSummary } from './PortfolioSummary'
import { TransactionHistory } from './TransactionHistory'
import { BitcoinPriceChart } from './BitcoinPriceChart'

interface Transaction {
  type: 'buy' | 'sell'
  amount: number
  price: number
  date: string
}

interface BitcoinPortfolioTrackerProps {
  readOnly: boolean
}

export function BitcoinPortfolioTracker({ readOnly }: BitcoinPortfolioTrackerProps) {
  const [transactions, setTransactions] = useState<Transaction[]>([])
  const [bitcoinPrice, setBitcoinPrice] = useState<number>(0)
  const [historicalPrices, setHistoricalPrices] = useState<{ date: string; price: number }[]>([])
  const [isLoading, setIsLoading] = useState(true)

  const saveTransactions = useCallback(() => {
    localStorage.setItem('bitcoinTransactions', JSON.stringify(transactions))
  }, [transactions])

  const fetchBitcoinPrice = useCallback(async () => {
    try {
      const response = await fetch('https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd')
      const data = await response.json()
      setBitcoinPrice(data.bitcoin.usd)
      return data.bitcoin.usd
    } catch (error) {
      console.error('Error fetching Bitcoin price:', error)
    }
  }, [])

  const fetchHistoricalPrices = useCallback(async () => {
    setIsLoading(true)
    try {
      const startDate = new Date('2024-01-01T00:00:00Z')
      const endDate = new Date()
      const response = await fetch(
        `https://api.coingecko.com/api/v3/coins/bitcoin/market_chart/range?vs_currency=usd&from=${Math.floor(
          startDate.getTime() / 1000
        )}&to=${Math.floor(endDate.getTime() / 1000)}`
      )
      const data = await response.json()
      const formattedData = data.prices
        .map(([timestamp, price]: [number, number]) => ({
          date: new Date(timestamp).toISOString(),
          price: Number(price.toFixed(2)),
        }))
        .filter((item: { price: number }) => !isNaN(item.price))
      
      setHistoricalPrices(formattedData)
    } catch (error) {
      console.error('Error fetching historical Bitcoin prices:', error)
    } finally {
      setIsLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchBitcoinPrice()
    fetchHistoricalPrices()
    const priceInterval = setInterval(fetchBitcoinPrice, 60000) // Update price every minute

    // Load saved transactions
    const savedTransactions = localStorage.getItem('bitcoinTransactions')
    if (savedTransactions) {
      setTransactions(JSON.parse(savedTransactions))
    }

    return () => clearInterval(priceInterval)
  }, [fetchBitcoinPrice, fetchHistoricalPrices])

  const addTransaction = useCallback((transaction: Transaction) => {
    const newTransactions = [...transactions, transaction]
    setTransactions(newTransactions)
    saveTransactions()
    
    // Update historical prices with the new transaction
    setHistoricalPrices(prevPrices => [
      ...prevPrices,
      { date: transaction.date, price: transaction.price }
    ])
  }, [transactions, saveTransactions])

  const clearTransactions = useCallback(() => {
    setTransactions([])
    localStorage.removeItem('bitcoinTransactions')
  }, [])

  const averageCost = useMemo(() => {
    const buyTransactions = transactions.filter(t => t.type === 'buy')
    const totalCost = buyTransactions.reduce((sum, t) => sum + t.amount * t.price, 0)
    const totalAmount = buyTransactions.reduce((sum, t) => sum + t.amount, 0)
    return totalAmount > 0 ? totalCost / totalAmount : 0
  }, [transactions])

  return (
    <div 
      className="min-h-screen w-full bg-cover bg-center bg-no-repeat p-4"
      style={{
        backgroundImage: 'url("https://hebbkx1anhila5yf.public.blob.vercel-storage.com/20241128_170910.jpg-ZP4Lnya6riH8uSEG7eoqz3Fv7CEaTu.jpeg")',
      }}
    >
      <div className="container mx-auto p-4 backdrop-blur-sm bg-black/30 rounded-lg">
        <h1 className="text-3xl font-bold mb-4 text-white">Andrés Strategic Reserve</h1>
        {!readOnly && (
          <TransactionForm 
            onAddTransaction={addTransaction} 
            currentPrice={bitcoinPrice} 
            onSaveTransaction={saveTransactions}
            onClearTransactions={clearTransactions}
          />
        )}
        <PortfolioSummary transactions={transactions} currentPrice={bitcoinPrice} />
        {isLoading ? (
          <p className="text-white">Cargando gráfico...</p>
        ) : (
          <BitcoinPriceChart data={historicalPrices} transactions={transactions} currentPrice={bitcoinPrice} />
        )}
        <TransactionHistory transactions={transactions} averageCost={averageCost} />
      </div>
    </div>
  )
}

