// Attach to a chart as a script. Prints only clock/bar epochs to Experts.
#property strict
void OnStart()
{
   PrintFormat("TradeForge clock TimeCurrent=%I64d TimeTradeServer=%I64d TimeGMT=%I64d TimeLocal=%I64d",
               (long)TimeCurrent(), (long)TimeTradeServer(), (long)TimeGMT(), (long)TimeLocal());
   MqlTick tick;
   if(SymbolInfoTick(_Symbol,tick))
      PrintFormat("TradeForge tick time=%I64d time_msc=%I64d",(long)tick.time,tick.time_msc);
   ENUM_TIMEFRAMES frames[2]={PERIOD_M1,PERIOD_M5};
   for(int i=0;i<ArraySize(frames);i++)
   {
      MqlRates rates[];
      ArraySetAsSeries(rates,true);
      int count=CopyRates(_Symbol,frames[i],0,3,rates);
      for(int bar=0;bar<count;bar++)
         PrintFormat("TradeForge frame=%s position=%d raw_time=%I64d",EnumToString(frames[i]),bar,
                     (long)rates[bar].time);
   }
}
