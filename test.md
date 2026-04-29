<!DOCTYPE html>
<html lang="en">
<head>
    <title>big: logs: 5,000 rows</title>
    <link rel="stylesheet" href="/-/static/app.css?ceb9b2">
    <meta name="viewport" content="width=device-width, initial-scale=1, shrink-to-fit=no">

<script>window.datasetteVersion = '1.0a28';</script>
<script src="/-/static/datasette-manager.js" defer></script>
<link rel="alternate" type="application/json+datasette" href="http://localhost:8001/big/logs.json?_format=markdown"><script src="/-/static/column-chooser.js" defer></script>
<script src="/-/static/table.js" defer></script>
<script src="/-/static/mobile-column-actions.js" defer></script>
<script>DATASETTE_ALLOW_FACET = true;</script>
<style>
@media only screen and (max-width: 576px) {
.rows-and-columns td:nth-of-type(1):before { content: "Link"; }
.rows-and-columns td:nth-of-type(2):before { content: "rowid"; }
.rows-and-columns td:nth-of-type(3):before { content: "id"; }
.rows-and-columns td:nth-of-type(4):before { content: "message"; }
}
</style>
</head>
<body class="table db-big table-logs">
<div class="not-footer">
<header class="hd"><nav>

  
  
    <p class="crumbs">
      
        <a href="/">home</a>
        
          /
        
      
        <a href="/big">big</a>
        
          /
        
      
        <a href="/big/logs">logs</a>
        
      
    </p>
  


    
    
</nav></header>



    



<section class="content">

<div class="page-header" style="border-color: #d86187">
    <h1>logs</h1>
</div>











    <h3>
        5,000 rows
        
    </h3>


<form class="core" class="filters" action="/big/logs" method="get">
    
    
    <div class="filter-row">
        <div class="select-wrapper">
            <select name="_filter_column">
                <option value="">- column -</option>
                
                      <option>rowid</option>
                
                      <option>id</option>
                
                      <option>message</option>
                
            </select>
        </div><div class="select-wrapper filter-op">
            <select name="_filter_op">
                
                    <option value="exact">=</option>
                
                    <option value="not">!=</option>
                
                    <option value="contains">contains</option>
                
                    <option value="notcontains">does not contain</option>
                
                    <option value="endswith">ends with</option>
                
                    <option value="startswith">starts with</option>
                
                    <option value="gt">&gt;</option>
                
                    <option value="gte">≥</option>
                
                    <option value="lt">&lt;</option>
                
                    <option value="lte">≤</option>
                
                    <option value="like">like</option>
                
                    <option value="notlike">not like</option>
                
                    <option value="glob">glob</option>
                
                    <option value="in">in</option>
                
                    <option value="notin">not in</option>
                
                    <option value="arraycontains">array contains</option>
                
                    <option value="arraynotcontains">array does not contain</option>
                
                    <option value="date">date</option>
                
                    <option value="isnull__1">is null</option>
                
                    <option value="notnull__1">is not null</option>
                
                    <option value="isblank__1">is blank</option>
                
                    <option value="notblank__1">is not blank</option>
                
            </select>
        </div><input type="text" name="_filter_value" class="filter-value">
    </div>
    <div class="filter-row">
        
            <div class="select-wrapper small-screen-only">
                <select name="_sort" id="sort_by">
                    <option value="">Sort...</option>
                    
                        
                    
                        
                            <option value="rowid" selected>Sort by rowid</option>
                        
                    
                        
                            <option value="id">Sort by id</option>
                        
                    
                        
                            <option value="message">Sort by message</option>
                        
                    
                </select>
            </div>
            <label class="sort_by_desc small-screen-only"><input type="checkbox" name="_sort_by_desc"> descending</label>
        
        
            <input type="hidden" name="_format" value="markdown">
        
        <input type="submit" value="Apply">
    </div>
</form>




    <p><a class="not-underlined" title="select rowid, id, message from logs order by rowid limit 101" href="/big?sql=select+rowid%2C+id%2C+message+from+logs+order+by+rowid+limit+101">&#x270e; <span class="underlined">View and edit SQL</span></a></p>


<p class="export-links">This data as <a href="/big/logs.json?_format=markdown">json</a>, <a href="/big/logs.csv?_format=markdown&amp;_size=max">CSV</a> (<a href="#export">advanced</a>)</p>






    <div class="facet-results">
    
</div>



<column-chooser></column-chooser>
<button class="choose-columns-mobile small-screen-only" onclick="openColumnChooser()">Choose columns</button>
<button type="button" class="column-actions-mobile small-screen-only">
    <svg aria-hidden="true" xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <circle cx="12" cy="12" r="3"></circle>
        <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"></path>
    </svg>
    <span>Column actions</span>
</button>
<script>
window._columnChooserData = {"allColumns": ["id", "message"], "primaryKeys": [], "selectedColumns": ["Link", "rowid", "id", "message"]};
</script>



<!-- above-table-panel is a hook node for plugins to attach to . Displays even if no data available -->
<div class="above-table-panel"> </div>

<div class="table-wrapper">
    <table class="rows-and-columns">
        <thead>
            <tr>
                
                    <th class="col-Link" scope="col" data-column="Link" data-column-type="" data-column-not-null="0" data-is-pk="0" data-is-link-column="1">
                        
                            Link
                        
                    </th>
                
                    <th class="col-rowid" scope="col" data-column="rowid" data-column-type="integer" data-column-not-null="0" data-is-pk="1">
                        
                            
                                <a href="/big/logs?_format=markdown&amp;_sort_desc=rowid" rel="nofollow">rowid&nbsp;▼</a>
                            
                        
                    </th>
                
                    <th class="col-id" scope="col" data-column="id" data-column-type="int" data-column-not-null="0" data-is-pk="0">
                        
                            
                                <a href="/big/logs?_format=markdown&amp;_sort=id" rel="nofollow">id</a>
                            
                        
                    </th>
                
                    <th class="col-message" scope="col" data-column="message" data-column-type="text" data-column-not-null="0" data-is-pk="0">
                        
                            
                                <a href="/big/logs?_format=markdown&amp;_sort=message" rel="nofollow">message</a>
                            
                        
                    </th>
                
            </tr>
        </thead>
        <tbody>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/1">1</a></td>
                
                    <td class="col-rowid type-int">1</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 1</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/2">2</a></td>
                
                    <td class="col-rowid type-int">2</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 2</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/3">3</a></td>
                
                    <td class="col-rowid type-int">3</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 3</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/4">4</a></td>
                
                    <td class="col-rowid type-int">4</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 4</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/5">5</a></td>
                
                    <td class="col-rowid type-int">5</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 5</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/6">6</a></td>
                
                    <td class="col-rowid type-int">6</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 6</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/7">7</a></td>
                
                    <td class="col-rowid type-int">7</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 7</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/8">8</a></td>
                
                    <td class="col-rowid type-int">8</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 8</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/9">9</a></td>
                
                    <td class="col-rowid type-int">9</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 9</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/10">10</a></td>
                
                    <td class="col-rowid type-int">10</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 10</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/11">11</a></td>
                
                    <td class="col-rowid type-int">11</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 11</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/12">12</a></td>
                
                    <td class="col-rowid type-int">12</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 12</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/13">13</a></td>
                
                    <td class="col-rowid type-int">13</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 13</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/14">14</a></td>
                
                    <td class="col-rowid type-int">14</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 14</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/15">15</a></td>
                
                    <td class="col-rowid type-int">15</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 15</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/16">16</a></td>
                
                    <td class="col-rowid type-int">16</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 16</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/17">17</a></td>
                
                    <td class="col-rowid type-int">17</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 17</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/18">18</a></td>
                
                    <td class="col-rowid type-int">18</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 18</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/19">19</a></td>
                
                    <td class="col-rowid type-int">19</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 19</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/20">20</a></td>
                
                    <td class="col-rowid type-int">20</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 20</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/21">21</a></td>
                
                    <td class="col-rowid type-int">21</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 21</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/22">22</a></td>
                
                    <td class="col-rowid type-int">22</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 22</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/23">23</a></td>
                
                    <td class="col-rowid type-int">23</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 23</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/24">24</a></td>
                
                    <td class="col-rowid type-int">24</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 24</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/25">25</a></td>
                
                    <td class="col-rowid type-int">25</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 25</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/26">26</a></td>
                
                    <td class="col-rowid type-int">26</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 26</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/27">27</a></td>
                
                    <td class="col-rowid type-int">27</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 27</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/28">28</a></td>
                
                    <td class="col-rowid type-int">28</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 28</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/29">29</a></td>
                
                    <td class="col-rowid type-int">29</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 29</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/30">30</a></td>
                
                    <td class="col-rowid type-int">30</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 30</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/31">31</a></td>
                
                    <td class="col-rowid type-int">31</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 31</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/32">32</a></td>
                
                    <td class="col-rowid type-int">32</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 32</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/33">33</a></td>
                
                    <td class="col-rowid type-int">33</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 33</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/34">34</a></td>
                
                    <td class="col-rowid type-int">34</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 34</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/35">35</a></td>
                
                    <td class="col-rowid type-int">35</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 35</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/36">36</a></td>
                
                    <td class="col-rowid type-int">36</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 36</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/37">37</a></td>
                
                    <td class="col-rowid type-int">37</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 37</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/38">38</a></td>
                
                    <td class="col-rowid type-int">38</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 38</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/39">39</a></td>
                
                    <td class="col-rowid type-int">39</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 39</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/40">40</a></td>
                
                    <td class="col-rowid type-int">40</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 40</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/41">41</a></td>
                
                    <td class="col-rowid type-int">41</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 41</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/42">42</a></td>
                
                    <td class="col-rowid type-int">42</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 42</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/43">43</a></td>
                
                    <td class="col-rowid type-int">43</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 43</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/44">44</a></td>
                
                    <td class="col-rowid type-int">44</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 44</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/45">45</a></td>
                
                    <td class="col-rowid type-int">45</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 45</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/46">46</a></td>
                
                    <td class="col-rowid type-int">46</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 46</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/47">47</a></td>
                
                    <td class="col-rowid type-int">47</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 47</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/48">48</a></td>
                
                    <td class="col-rowid type-int">48</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 48</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/49">49</a></td>
                
                    <td class="col-rowid type-int">49</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 49</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/50">50</a></td>
                
                    <td class="col-rowid type-int">50</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 50</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/51">51</a></td>
                
                    <td class="col-rowid type-int">51</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 51</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/52">52</a></td>
                
                    <td class="col-rowid type-int">52</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 52</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/53">53</a></td>
                
                    <td class="col-rowid type-int">53</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 53</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/54">54</a></td>
                
                    <td class="col-rowid type-int">54</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 54</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/55">55</a></td>
                
                    <td class="col-rowid type-int">55</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 55</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/56">56</a></td>
                
                    <td class="col-rowid type-int">56</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 56</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/57">57</a></td>
                
                    <td class="col-rowid type-int">57</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 57</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/58">58</a></td>
                
                    <td class="col-rowid type-int">58</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 58</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/59">59</a></td>
                
                    <td class="col-rowid type-int">59</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 59</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/60">60</a></td>
                
                    <td class="col-rowid type-int">60</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 60</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/61">61</a></td>
                
                    <td class="col-rowid type-int">61</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 61</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/62">62</a></td>
                
                    <td class="col-rowid type-int">62</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 62</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/63">63</a></td>
                
                    <td class="col-rowid type-int">63</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 63</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/64">64</a></td>
                
                    <td class="col-rowid type-int">64</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 64</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/65">65</a></td>
                
                    <td class="col-rowid type-int">65</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 65</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/66">66</a></td>
                
                    <td class="col-rowid type-int">66</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 66</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/67">67</a></td>
                
                    <td class="col-rowid type-int">67</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 67</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/68">68</a></td>
                
                    <td class="col-rowid type-int">68</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 68</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/69">69</a></td>
                
                    <td class="col-rowid type-int">69</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 69</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/70">70</a></td>
                
                    <td class="col-rowid type-int">70</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 70</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/71">71</a></td>
                
                    <td class="col-rowid type-int">71</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 71</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/72">72</a></td>
                
                    <td class="col-rowid type-int">72</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 72</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/73">73</a></td>
                
                    <td class="col-rowid type-int">73</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 73</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/74">74</a></td>
                
                    <td class="col-rowid type-int">74</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 74</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/75">75</a></td>
                
                    <td class="col-rowid type-int">75</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 75</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/76">76</a></td>
                
                    <td class="col-rowid type-int">76</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 76</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/77">77</a></td>
                
                    <td class="col-rowid type-int">77</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 77</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/78">78</a></td>
                
                    <td class="col-rowid type-int">78</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 78</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/79">79</a></td>
                
                    <td class="col-rowid type-int">79</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 79</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/80">80</a></td>
                
                    <td class="col-rowid type-int">80</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 80</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/81">81</a></td>
                
                    <td class="col-rowid type-int">81</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 81</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/82">82</a></td>
                
                    <td class="col-rowid type-int">82</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 82</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/83">83</a></td>
                
                    <td class="col-rowid type-int">83</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 83</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/84">84</a></td>
                
                    <td class="col-rowid type-int">84</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 84</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/85">85</a></td>
                
                    <td class="col-rowid type-int">85</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 85</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/86">86</a></td>
                
                    <td class="col-rowid type-int">86</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 86</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/87">87</a></td>
                
                    <td class="col-rowid type-int">87</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 87</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/88">88</a></td>
                
                    <td class="col-rowid type-int">88</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 88</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/89">89</a></td>
                
                    <td class="col-rowid type-int">89</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 89</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/90">90</a></td>
                
                    <td class="col-rowid type-int">90</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 90</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/91">91</a></td>
                
                    <td class="col-rowid type-int">91</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 91</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/92">92</a></td>
                
                    <td class="col-rowid type-int">92</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 92</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/93">93</a></td>
                
                    <td class="col-rowid type-int">93</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 93</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/94">94</a></td>
                
                    <td class="col-rowid type-int">94</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 94</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/95">95</a></td>
                
                    <td class="col-rowid type-int">95</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 95</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/96">96</a></td>
                
                    <td class="col-rowid type-int">96</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 96</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/97">97</a></td>
                
                    <td class="col-rowid type-int">97</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 97</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/98">98</a></td>
                
                    <td class="col-rowid type-int">98</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 98</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/99">99</a></td>
                
                    <td class="col-rowid type-int">99</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 99</td>
                
            </tr>
        
            <tr>
                
                    <td class="col-Link type-pk"><a href="/big/logs/100">100</a></td>
                
                    <td class="col-rowid type-int">100</td>
                
                    <td class="col-id type-none">&nbsp;</td>
                
                    <td class="col-message type-str">Log message number 100</td>
                
            </tr>
        
        </tbody>
    </table>
</div>




     <p><a href="http://localhost:8001/big/logs?_format=markdown&amp;_next=100">Next page</a></p>



    <div id="export" class="advanced-export">
        <h3>Advanced export</h3>
        <p>JSON shape:
            <a href="/big/logs.json?_format=markdown">default</a>,
            <a href="/big/logs.json?_format=markdown&amp;_shape=array">array</a>,
            <a href="/big/logs.json?_format=markdown&amp;_shape=array&amp;_nl=on">newline-delimited</a>
        </p>
        <form class="core" action="/big/logs.csv" method="get">
            <p>
                CSV options:
                <label><input type="checkbox" name="_dl"> download file</label>
                
                <label><input type="checkbox" name="_stream"> stream all rows</label>
                <input type="submit" value="Export CSV">
                
                    <input type="hidden" name="_format" value="markdown">
                
                    <input type="hidden" name="_size" value="max">
                
            </p>
        </form>
    </div>



    <pre class="wrapped-sql">CREATE TABLE logs (id int, message text);</pre>





<script>
document.addEventListener('DOMContentLoaded', function() {
    const countLink = document.querySelector('a.count-sql');
    if (countLink) {
        countLink.addEventListener('click', async function(ev) {
            ev.preventDefault();
            // Replace countLink with span with same style attribute
            const span = document.createElement('span');
            span.textContent = 'counting...';
            span.setAttribute('style', countLink.getAttribute('style'));
            countLink.replaceWith(span);
            countLink.setAttribute('disabled', 'disabled');
            let url = countLink.href.replace(/(\?|$)/, '.json$1');
            try {
                const response = await fetch(url);
                console.log({response});
                const data = await response.json();
                console.log({data});
                if (!response.ok) {
                    console.log('throw error');
                    throw new Error(data.title || data.error);
                }
                const count = data['rows'][0]['count(*)'];
                const formattedCount = count.toLocaleString();
                span.closest('h3').textContent = formattedCount + ' rows';
            } catch (error) {
                console.log('Update', span, 'with error message', error);
                span.textContent = error.message;
                span.style.color = 'red';
            }
        });
    }
});
</script>



</section>
</div>
<footer class="ft">Powered by <a href="https://datasette.io/" title="Datasette v1.0a28">Datasette</a>
&middot; Queries took 1.2ms

    
    
    
</footer>

<script>
document.body.addEventListener('click', (ev) => {
    /* Close any open details elements that this click is outside of */
    var target = ev.target;
    var detailsClickedWithin = null;
    while (target && target.tagName != 'DETAILS') {
        target = target.parentNode;
    }
    if (target && target.tagName == 'DETAILS') {
        detailsClickedWithin = target;
    }
    Array.from(document.querySelectorAll('details.details-menu')).filter(
        (details) => details.open && details != detailsClickedWithin
    ).forEach(details => details.open = false);
});
</script>



<!-- Templates considered: table-big-logs.html, *table.html -->
<script src="/-/static/navigation-search.js" defer></script>
<navigation-search url="/-/tables"></navigation-search>
</body>
</html>