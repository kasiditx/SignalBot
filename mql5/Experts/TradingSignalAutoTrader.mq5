//+------------------------------------------------------------------+
//| TradingSignalAutoTrader.mq5                                      |
//| Reads Python signal order intents and optionally places MT5 trades |
//+------------------------------------------------------------------+
#property strict
#property version   "1.00"
#property description "Reads trading_signal_order.csv from MQL5/Files and executes controlled market orders."

#include <Trade/Trade.mqh>

input string InpOrderFile       = "trading_signal_order.csv";
input bool   InpDryRun          = true;
input int    InpIntervalSeconds = 1;
input int    InpMaxSpreadPoints = 500;
input int    InpDeviationPoints = 50;
input int    InpMaxPositions    = 1;
input long   InpMagicNumber     = 20260515;

CTrade trade;
string last_nonce = "";

struct OrderIntent
{
   string nonce;
   string symbol;
   string action;
   double volume;
   double entry;
   double stop_loss;
   double take_profit;
   long magic_number;
   string comment;
};

int OnInit()
{
   if(InpIntervalSeconds < 1)
   {
      Print("InpIntervalSeconds must be at least 1");
      return INIT_PARAMETERS_INCORRECT;
   }
   if(InpMaxPositions < 0)
   {
      Print("InpMaxPositions must be zero or greater");
      return INIT_PARAMETERS_INCORRECT;
   }

   trade.SetExpertMagicNumber(InpMagicNumber);
   trade.SetDeviationInPoints(InpDeviationPoints);
   EventSetTimer(InpIntervalSeconds);
   Print("TradingSignalAutoTrader started. DryRun=", InpDryRun, " OrderFile=", InpOrderFile);
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   EventKillTimer();
   Print("TradingSignalAutoTrader stopped. Reason: ", reason);
}

void OnTick()
{
   // Execution is timer-based so duplicate ticks do not repeatedly read the file.
}

void OnTimer()
{
   OrderIntent intent;
   if(!ReadOrderIntent(intent))
      return;

   if(intent.nonce == last_nonce)
      return;

   if(!ValidateIntent(intent))
      return;

   last_nonce = intent.nonce;

   if(InpDryRun)
   {
      Print("DryRun order intent accepted. Nonce=", intent.nonce, " Action=", intent.action, " Volume=", intent.volume,
            " SL=", intent.stop_loss, " TP=", intent.take_profit);
      return;
   }

   ExecuteIntent(intent);
}

bool ReadOrderIntent(OrderIntent &intent)
{
   int handle = FileOpen(InpOrderFile, FILE_READ | FILE_CSV | FILE_ANSI, ',');
   if(handle == INVALID_HANDLE)
      return false;

   // Header row.
   for(int column = 0; column < 10 && !FileIsEnding(handle); column++)
      FileReadString(handle);

   if(FileIsEnding(handle))
   {
      FileClose(handle);
      return false;
   }

   intent.nonce = FileReadString(handle);
   FileReadString(handle); // created_at
   intent.symbol = FileReadString(handle);
   intent.action = FileReadString(handle);
   intent.volume = StringToDouble(FileReadString(handle));
   intent.entry = StringToDouble(FileReadString(handle));
   intent.stop_loss = StringToDouble(FileReadString(handle));
   intent.take_profit = StringToDouble(FileReadString(handle));
   intent.magic_number = (long)StringToInteger(FileReadString(handle));
   intent.comment = FileReadString(handle);

   FileClose(handle);
   return intent.nonce != "";
}

bool ValidateIntent(const OrderIntent &intent)
{
   if(intent.symbol != _Symbol)
   {
      Print("Order symbol mismatch. Chart=", _Symbol, " Intent=", intent.symbol);
      return false;
   }
   if(intent.magic_number != InpMagicNumber)
   {
      Print("Magic number mismatch. EA=", InpMagicNumber, " Intent=", intent.magic_number);
      return false;
   }
   if(intent.action != "BUY" && intent.action != "SELL")
   {
      Print("Unsupported action: ", intent.action);
      return false;
   }
   if(intent.volume <= 0 || intent.stop_loss <= 0 || intent.take_profit <= 0)
   {
      Print("Invalid order levels or volume. Volume=", intent.volume, " SL=", intent.stop_loss, " TP=", intent.take_profit);
      return false;
   }
   if(intent.action == "BUY" && !(intent.stop_loss < intent.entry && intent.entry < intent.take_profit))
   {
      Print("Invalid BUY levels. Entry=", intent.entry, " SL=", intent.stop_loss, " TP=", intent.take_profit);
      return false;
   }
   if(intent.action == "SELL" && !(intent.take_profit < intent.entry && intent.entry < intent.stop_loss))
   {
      Print("Invalid SELL levels. Entry=", intent.entry, " SL=", intent.stop_loss, " TP=", intent.take_profit);
      return false;
   }

   long spread = SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
   if(spread > InpMaxSpreadPoints)
   {
      Print("Spread too high. Spread=", spread, " Max=", InpMaxSpreadPoints);
      return false;
   }

   int open_positions = CountOpenPositions();
   if(open_positions >= InpMaxPositions)
   {
      Print("Max positions reached. Open=", open_positions, " Max=", InpMaxPositions);
      return false;
   }

   return true;
}

void ExecuteIntent(const OrderIntent &intent)
{
   double volume = NormalizeVolume(intent.volume);
   if(volume <= 0)
   {
      Print("Normalized volume is invalid: ", volume);
      return;
   }

   bool ok = false;
   if(intent.action == "BUY")
      ok = trade.Buy(volume, intent.symbol, 0.0, intent.stop_loss, intent.take_profit, intent.comment);
   else
      ok = trade.Sell(volume, intent.symbol, 0.0, intent.stop_loss, intent.take_profit, intent.comment);

   if(!ok)
   {
      Print("Order failed. Retcode=", trade.ResultRetcode(), " Description=", trade.ResultRetcodeDescription());
      return;
   }

   Print("Order placed. Ticket=", trade.ResultOrder(), " Action=", intent.action, " Volume=", volume,
         " SL=", intent.stop_loss, " TP=", intent.take_profit);
}

int CountOpenPositions()
{
   int count = 0;
   for(int index = PositionsTotal() - 1; index >= 0; index--)
   {
      ulong ticket = PositionGetTicket(index);
      if(ticket == 0)
         continue;
      if(!PositionSelectByTicket(ticket))
         continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol)
         continue;
      if(PositionGetInteger(POSITION_MAGIC) != InpMagicNumber)
         continue;
      count++;
   }
   return count;
}

double NormalizeVolume(const double requested_volume)
{
   double min_volume = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double max_volume = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   if(step <= 0)
      return 0.0;

   double clamped = MathMax(min_volume, MathMin(requested_volume, max_volume));
   double normalized = MathFloor(clamped / step) * step;
   return NormalizeDouble(normalized, 2);
}
