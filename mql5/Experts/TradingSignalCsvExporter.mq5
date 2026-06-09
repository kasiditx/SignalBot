//+------------------------------------------------------------------+
//| TradingSignalCsvExporter.mq5                                     |
//| Exports multi-timeframe OHLCV data for the Python signal bot.     |
//| This Expert Advisor does not place, modify, or close orders.      |
//+------------------------------------------------------------------+
#property strict
#property version   "1.00"
#property description "Exports OHLCV CSV for an external signal-only bot. No trading operations."

input int             InpBarsD1    = 800;
input int             InpBarsH4    = 5000;
input int             InpBarsH1    = 20000;
input int             InpBarsM30   = 40000;
input int             InpBarsM15   = 80000;
input int             InpBarsM5    = 120000;
input int             InpIntervalSeconds = 5;
input bool            InpExportD1  = true;
input bool            InpExportH4  = true;
input bool            InpExportH1  = true;
input bool            InpExportM30 = true;
input bool            InpExportM15 = true;
input bool            InpExportM5  = true;

int OnInit()
{
   if(InpBarsM5 < 60)
   {
      Print("InpBarsM5 must be at least 60");
      return INIT_PARAMETERS_INCORRECT;
   }

   if(InpIntervalSeconds < 5)
   {
      Print("InpIntervalSeconds must be at least 5");
      return INIT_PARAMETERS_INCORRECT;
   }

   EventSetTimer(InpIntervalSeconds);
   ExportAllRates();
   Print("TradingSignalCsvExporter started. Multi-timeframe CSV export enabled.");
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   EventKillTimer();
   Print("TradingSignalCsvExporter stopped. Reason: ", reason);
}

void OnTick()
{
   // Export is timer-based to avoid excessive file writes on fast ticks.
}

void OnTimer()
{
   ExportAllRates();
}

void ExportAllRates()
{
   if(InpExportD1)
      ExportRatesFor(PERIOD_D1, "mt5_ohlcv_D1.csv", InpBarsD1);
   if(InpExportH4)
      ExportRatesFor(PERIOD_H4, "mt5_ohlcv_H4.csv", InpBarsH4);
   if(InpExportH1)
      ExportRatesFor(PERIOD_H1, "mt5_ohlcv_H1.csv", InpBarsH1);
   if(InpExportM30)
      ExportRatesFor(PERIOD_M30, "mt5_ohlcv_M30.csv", InpBarsM30);
   if(InpExportM15)
      ExportRatesFor(PERIOD_M15, "mt5_ohlcv_M15.csv", InpBarsM15);
   if(InpExportM5)
   {
      ExportRatesFor(PERIOD_M5, "mt5_ohlcv_M5.csv", InpBarsM5);
      ExportRatesFor(PERIOD_M5, "mt5_ohlcv.csv", InpBarsM5);
   }
}

void ExportRatesFor(ENUM_TIMEFRAMES timeframe, string file_name, int requested_bars)
{
   int available_bars = Bars(_Symbol, timeframe);
   if(available_bars <= 0)
   {
      Print("No available bars yet. Symbol=", _Symbol, " timeframe=", EnumToString(timeframe), " error=", GetLastError());
      return;
   }

   int bars_to_copy = MathMin(requested_bars, available_bars);
   if(bars_to_copy < 60)
   {
      Print("Not enough bars yet. Symbol=", _Symbol, " timeframe=", EnumToString(timeframe), " available=", available_bars);
      return;
   }

   MqlRates rates[];
   ArraySetAsSeries(rates, true);

   int copied = CopyRates(_Symbol, timeframe, 0, bars_to_copy, rates);
   if(copied <= 0)
   {
      Print("CopyRates failed. Symbol=", _Symbol, " timeframe=", EnumToString(timeframe), " requested=", bars_to_copy, " available=", available_bars, " error=", GetLastError());
      return;
   }

   int handle = FileOpen(file_name, FILE_WRITE | FILE_CSV | FILE_ANSI, ',');
   if(handle == INVALID_HANDLE)
   {
      Print("FileOpen failed. File=", file_name, " error=", GetLastError());
      return;
   }

   FileWrite(handle, "timestamp", "open", "high", "low", "close", "volume");

   for(int index = copied - 1; index >= 0; index--)
   {
      FileWrite(
         handle,
         TimeToString(rates[index].time, TIME_DATE | TIME_MINUTES),
         DoubleToString(rates[index].open, _Digits),
         DoubleToString(rates[index].high, _Digits),
         DoubleToString(rates[index].low, _Digits),
         DoubleToString(rates[index].close, _Digits),
         (long)rates[index].tick_volume
      );
   }

   FileClose(handle);
}
