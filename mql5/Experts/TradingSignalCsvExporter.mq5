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
input int             InpHistoryMonths = 13;
input int             InpIntervalSeconds = 5;
input int             InpCoverageWarningSeconds = 300;
input bool            InpExportD1  = true;
input bool            InpExportH4  = true;
input bool            InpExportH1  = true;
input bool            InpExportM30 = true;
input bool            InpExportM15 = true;
input bool            InpExportM5  = true;

datetime last_coverage_warning_at = 0;
const int HISTORY_MONTH_DAYS = 31;
const int SECONDS_PER_DAY = 86400;

int OnInit()
{
   if(InpBarsM5 < 60)
   {
      Print("InpBarsM5 must be at least 60");
      return INIT_PARAMETERS_INCORRECT;
   }

   if(InpHistoryMonths < 0)
   {
      Print("InpHistoryMonths must be zero or greater");
      return INIT_PARAMETERS_INCORRECT;
   }

   if(InpIntervalSeconds < 5)
   {
      Print("InpIntervalSeconds must be at least 5");
      return INIT_PARAMETERS_INCORRECT;
   }

   if(InpCoverageWarningSeconds < 30)
   {
      Print("InpCoverageWarningSeconds must be at least 30");
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
   if(requested_bars < 60)
   {
      Print("Requested bars must be at least 60. Symbol=", _Symbol, " timeframe=", EnumToString(timeframe), " requested=", requested_bars);
      return;
   }

   MqlRates rates[];
   ArraySetAsSeries(rates, true);

   datetime requested_start = RequestedHistoryStart();
   int copied = 0;
   if(requested_start > 0)
      copied = CopyRates(_Symbol, timeframe, requested_start, TimeCurrent(), rates);
   else
      copied = CopyRates(_Symbol, timeframe, 0, requested_bars, rates);

   if(copied <= 0)
   {
      int available_bars = Bars(_Symbol, timeframe);
      Print("CopyRates failed. Symbol=", _Symbol, " timeframe=", EnumToString(timeframe), " requested_start=", FormatTime(requested_start),
            " requested_bars=", requested_bars, " available=", available_bars, " error=", GetLastError());
      return;
   }

   if(copied > requested_bars)
      copied = requested_bars;

   WarnIfCoverageIsShort(timeframe, requested_start, rates[copied - 1].time, copied);

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

datetime RequestedHistoryStart()
{
   if(InpHistoryMonths <= 0)
      return 0;

   long seconds = (long)InpHistoryMonths * HISTORY_MONTH_DAYS * SECONDS_PER_DAY;
   return (datetime)(TimeCurrent() - seconds);
}

void WarnIfCoverageIsShort(ENUM_TIMEFRAMES timeframe, datetime requested_start, datetime first_exported, int copied)
{
   if(requested_start <= 0)
      return;

   int period_seconds = PeriodSeconds(timeframe);
   if(period_seconds <= 0)
      period_seconds = 60;

   if(first_exported <= requested_start + period_seconds)
      return;

   datetime now = TimeCurrent();
   if(last_coverage_warning_at > 0 && now - last_coverage_warning_at < InpCoverageWarningSeconds)
      return;

   last_coverage_warning_at = now;
   Print("History coverage is still short. Symbol=", _Symbol, " timeframe=", EnumToString(timeframe),
         " requested_start=", FormatTime(requested_start), " first_exported=", FormatTime(first_exported),
         " copied=", copied, ". Open MT5 History Center/scroll chart back, increase Tools > Options > Charts > Max bars, then reattach exporter.");
}

string FormatTime(datetime value)
{
   if(value <= 0)
      return "latest-bars";
   return TimeToString(value, TIME_DATE | TIME_MINUTES);
}
