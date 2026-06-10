//+------------------------------------------------------------------+
//| TradingSignalAutoTrader.mq5                                      |
//| Reads Python signal order intents and optionally places MT5 trades |
//+------------------------------------------------------------------+
#property strict
#property version   "1.00"
#property description "Reads trading_signal_order.csv from MQL5/Files and executes controlled market orders."

#include <Trade/Trade.mqh>

input string InpOrderFile       = "trading_signal_order.csv";
input bool   InpDryRun          = false;
input int    InpIntervalSeconds = 1;
input int    InpMaxSpreadPoints = 300;
input int    InpDeviationPoints = 50;
input int    InpMaxPositions    = 1;
input long   InpMagicNumber     = 20260515;
input bool   InpDeleteOrderFileAfterProcessing = true;
input double InpMaxActualRiskPercent = 25.0;
input bool   InpEnableBreakEven = true;
input double InpBreakEvenTriggerR = 0.6;
input int    InpBreakEvenOffsetPoints = 0;
input bool   InpEnablePartialClose = false;
input double InpPartialCloseTriggerR = 1.0;
input double InpPartialClosePercent = 50.0;

CTrade trade;
string last_nonce = "";
const int ORDER_FIELD_COUNT = 11;

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
   double actual_risk_percent;
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
   if(InpBreakEvenTriggerR <= 0)
   {
      Print("InpBreakEvenTriggerR must be greater than zero");
      return INIT_PARAMETERS_INCORRECT;
   }
   if(InpPartialCloseTriggerR <= 0)
   {
      Print("InpPartialCloseTriggerR must be greater than zero");
      return INIT_PARAMETERS_INCORRECT;
   }
   if(InpPartialClosePercent <= 0 || InpPartialClosePercent >= 100)
   {
      Print("InpPartialClosePercent must be greater than zero and lower than 100");
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
   ManageOpenPositions();

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
      DeleteProcessedOrderFile(intent.nonce);
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
   for(int column = 0; column < ORDER_FIELD_COUNT && !FileIsEnding(handle); column++)
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
   intent.actual_risk_percent = StringToDouble(FileReadString(handle));

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
   if(InpMaxActualRiskPercent > 0 && intent.actual_risk_percent > InpMaxActualRiskPercent)
   {
      Print("Actual risk too high. Risk=", intent.actual_risk_percent, "% Max=", InpMaxActualRiskPercent, "%");
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
   DeleteProcessedOrderFile(intent.nonce);
}

void DeleteProcessedOrderFile(const string nonce)
{
   if(!InpDeleteOrderFileAfterProcessing)
      return;

   if(FileDelete(InpOrderFile))
   {
      Print("Processed order file deleted. Nonce=", nonce, " File=", InpOrderFile);
      return;
   }

   Print("Processed order file could not be deleted. Nonce=", nonce, " File=", InpOrderFile, " Error=", GetLastError());
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

void ManageOpenPositions()
{
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

      ManagePosition(ticket);
   }
}

void ManagePosition(const ulong ticket)
{
   long position_type = PositionGetInteger(POSITION_TYPE);
   double open_price = PositionGetDouble(POSITION_PRICE_OPEN);
   double stop_loss = PositionGetDouble(POSITION_SL);
   double take_profit = PositionGetDouble(POSITION_TP);
   double volume = PositionGetDouble(POSITION_VOLUME);
   if(open_price <= 0 || stop_loss <= 0 || volume <= 0)
      return;

   double risk_distance = MathAbs(open_price - stop_loss);
   if(risk_distance <= 0)
      return;

   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double current_price = position_type == POSITION_TYPE_BUY ? bid : ask;
   if(current_price <= 0)
      return;

   double profit_distance = position_type == POSITION_TYPE_BUY ? current_price - open_price : open_price - current_price;
   if(profit_distance <= 0)
      return;

   if(InpEnableBreakEven && profit_distance >= risk_distance * InpBreakEvenTriggerR)
      MoveStopToBreakEven(ticket, position_type, stop_loss, take_profit, open_price);
}

bool IsStopAtBreakEven(const long position_type, const double stop_loss, const double open_price)
{
   if(position_type == POSITION_TYPE_BUY)
      return stop_loss >= open_price;
   return stop_loss <= open_price;
}

void TryPartialClose(const ulong ticket, const double current_volume)
{
   double min_volume = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double close_volume = NormalizeVolumeForPartial(current_volume * (InpPartialClosePercent / 100.0));
   if(close_volume < min_volume)
      return;
   if(current_volume - close_volume < min_volume)
      return;

   if(!trade.PositionClosePartial(ticket, close_volume))
   {
      Print("Partial close failed. Ticket=", ticket, " Volume=", close_volume,
            " Retcode=", trade.ResultRetcode(), " Description=", trade.ResultRetcodeDescription());
      return;
   }

   Print("Partial close placed. Ticket=", ticket, " Volume=", close_volume);
}

void MoveStopToBreakEven(
   const ulong ticket,
   const long position_type,
   const double current_stop_loss,
   const double take_profit,
   const double open_price
)
{
   double offset = InpBreakEvenOffsetPoints * _Point;
   double break_even_stop = position_type == POSITION_TYPE_BUY ? open_price + offset : open_price - offset;
   if(position_type == POSITION_TYPE_BUY && current_stop_loss >= break_even_stop)
      return;
   if(position_type == POSITION_TYPE_SELL && current_stop_loss <= break_even_stop)
      return;

   if(!trade.PositionModify(ticket, NormalizeDouble(break_even_stop, _Digits), take_profit))
   {
      Print("Breakeven modify failed. Ticket=", ticket, " SL=", break_even_stop,
            " Retcode=", trade.ResultRetcode(), " Description=", trade.ResultRetcodeDescription());
      return;
   }

   Print("Stop moved to breakeven. Ticket=", ticket, " SL=", break_even_stop);
}

double NormalizeVolumeForPartial(const double requested_volume)
{
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   if(step <= 0)
      return 0.0;
   return NormalizeDouble(MathFloor(requested_volume / step) * step, 3);
}

double NormalizeVolume(const double requested_volume)
{
   double min_volume = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double max_volume = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   if(step <= 0)
      return 0.0;

   if(requested_volume < min_volume)
   {
      Print("Requested volume is below broker minimum. Requested=", requested_volume, " Min=", min_volume);
      return 0.0;
   }
   if(requested_volume > max_volume)
   {
      Print("Requested volume is above broker maximum. Requested=", requested_volume, " Max=", max_volume);
      return 0.0;
   }

   double clamped = MathMin(requested_volume, max_volume);
   double normalized = MathFloor(clamped / step) * step;
   if(normalized < min_volume)
   {
      Print("Normalized volume is below broker minimum. Normalized=", normalized, " Min=", min_volume);
      return 0.0;
   }
   return NormalizeDouble(normalized, 3);
}
